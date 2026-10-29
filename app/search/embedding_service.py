"""Cached OpenAI embeddings; case vectors are built only by background work."""
from functools import lru_cache
from time import monotonic
from pathlib import Path

import httpx
import numpy as np
from openai import OpenAI, APIError, AuthenticationError, PermissionDeniedError, RateLimitError

from app import config
from app.db.repository import connect, data_version
from app.search.documents import EMBEDDING_DIMENSIONS, bounded_text, text_hash

BATCH_SIZE = 32
INDEX_BUDGET_SECONDS = 60


class SearchServiceError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def embed_texts(db_path, texts, purpose, settings=None):
    settings = settings or config.get_ai_settings()
    if not settings.api_key:
        raise SearchServiceError("NOT_CONFIGURED")
    timeout = 6 if purpose == "QUERY" else 20
    try:
        with OpenAI(api_key=settings.api_key,base_url="https://api.openai.com/v1",max_retries=0,
                    timeout=httpx.Timeout(timeout,connect=3)) as client:
            response = client.embeddings.create(model=settings.embedding_model,input=texts,
                                                 dimensions=EMBEDDING_DIMENSIONS,encoding_format="float")
    except (AuthenticationError,PermissionDeniedError):
        raise SearchServiceError("AUTH_FAILED") from None
    except RateLimitError:
        raise SearchServiceError("RATE_LIMITED") from None
    except APIError:
        raise SearchServiceError("UNAVAILABLE") from None
    items = sorted(response.data,key=lambda item:item.index)
    if [item.index for item in items] != list(range(len(texts))):
        raise SearchServiceError("INVALID_RESPONSE")
    vectors = np.asarray([item.embedding for item in items],dtype=np.float32)
    if vectors.shape != (len(texts),EMBEDDING_DIMENSIONS) or not np.isfinite(vectors).all():
        raise SearchServiceError("INVALID_RESPONSE")
    lengths = np.linalg.norm(vectors,axis=1,keepdims=True)
    if (lengths <= 0).any():
        raise SearchServiceError("INVALID_RESPONSE")
    vectors /= lengths
    tokens = response.usage.total_tokens
    with connect(db_path) as conn:
        conn.execute("INSERT INTO search_usage(purpose,model,input_tokens,input_count) VALUES (?,?,?,?)",
                     (purpose,settings.embedding_model,tokens,len(texts)))
    return vectors,tokens


def index_status(db_path):
    model = config.get_ai_settings().embedding_model
    with connect(db_path) as conn:
        row = conn.execute("""SELECT COUNT(*) AS total,COUNT(e.content_hash) AS ready
                              FROM search_documents d LEFT JOIN embedding_cache e
                              ON e.content_hash=d.content_hash AND e.model=? AND e.dimensions=?""",
                           (model,EMBEDDING_DIMENSIONS)).fetchone()
    return {"total":row["total"],"ready":row["ready"],"pending":row["total"]-row["ready"],"model":model}


def build_index(db_path, source_id=None, *, progress=None):
    settings = config.get_ai_settings()
    stats = {"status":"NOT_CONFIGURED","model":settings.embedding_model,"request_count":0,
             "input_tokens":0,"embedded_texts":0,"error_code":None}
    if not settings.api_key:
        return stats
    deadline = monotonic() + INDEX_BUDGET_SECONDS
    stats["status"] = "COMPLETED"
    while True:
        with connect(db_path) as conn:
            sql = """SELECT DISTINCT d.content_hash,d.document_text FROM search_documents d
                     JOIN cases c ON c.case_id=d.case_id WHERE NOT EXISTS (
                     SELECT 1 FROM embedding_cache e WHERE e.content_hash=d.content_hash
                     AND e.model=? AND e.dimensions=?)"""
            params = [settings.embedding_model,EMBEDDING_DIMENSIONS]
            if source_id is not None:
                sql += " AND c.source_id=?"
                params.append(source_id)
            sql += " LIMIT ?"
            rows = conn.execute(sql,[*params,BATCH_SIZE]).fetchall()
        if not rows:
            break
        if monotonic() >= deadline:
            stats.update(status="PARTIAL",error_code="BUDGET_REACHED")
            break
        if progress:
            progress("Preparing semantic search",stats["embedded_texts"],stats["embedded_texts"]+len(rows))
        try:
            stats["request_count"] += 1
            vectors,tokens = embed_texts(db_path,[row["document_text"] for row in rows],"INDEX",settings)
        except SearchServiceError as exc:
            stats.update(status="FAILED",error_code=exc.code)
            break
        stats["input_tokens"] += tokens
        stats["embedded_texts"] += len(rows)
        with connect(db_path) as conn:
            conn.executemany("INSERT OR IGNORE INTO embedding_cache(model,dimensions,content_hash,vector) VALUES (?,?,?,?)",
                             [(settings.embedding_model,EMBEDDING_DIMENSIONS,row["content_hash"],vector.tobytes())
                              for row,vector in zip(rows,vectors)])
            # Stale in-flight results remain cached by their old hash, never attached
            # to a changed document. Updating readiness invalidates displayed results.
            conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
    return stats


def query_vector(db_path, query):
    settings = config.get_ai_settings()
    text = bounded_text(" ".join(query.split()).casefold())
    fingerprint = text_hash(text)
    with connect(db_path) as conn:
        row = conn.execute("SELECT vector FROM embedding_cache WHERE model=? AND dimensions=? AND content_hash=?",
                           (settings.embedding_model,EMBEDDING_DIMENSIONS,fingerprint)).fetchone()
    if row:
        return np.frombuffer(row[0],dtype=np.float32),True
    vectors,_ = embed_texts(db_path,[text],"QUERY",settings)
    with connect(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO embedding_cache(model,dimensions,content_hash,vector) VALUES (?,?,?,?)",
                     (settings.embedding_model,EMBEDDING_DIMENSIONS,fingerprint,vectors[0].tobytes()))
    return vectors[0],False


@lru_cache(maxsize=4)
def _matrix(db_path, model, revision, file_identity=None):
    with connect(db_path) as conn:
        rows = conn.execute("""SELECT d.case_id,e.vector FROM search_documents d JOIN embedding_cache e
                               ON e.content_hash=d.content_hash WHERE e.model=? AND e.dimensions=? ORDER BY d.case_id""",
                            (model,EMBEDDING_DIMENSIONS)).fetchall()
    matrix = np.stack([np.frombuffer(row["vector"],dtype=np.float32) for row in rows]) if rows else np.empty((0,EMBEDDING_DIMENSIONS),dtype=np.float32)
    matrix.setflags(write=False)
    return [row["case_id"] for row in rows],matrix


def semantic_scores(db_path, vector):
    model = config.get_ai_settings().embedding_model
    info = Path(db_path).stat()
    identity = (info.st_ino,getattr(info,"st_birthtime_ns",info.st_ctime_ns))
    ids,matrix = _matrix(str(db_path),model,data_version(db_path),identity)
    # This small local corpus benefits from avoiding BLAS thread startup.
    return dict(zip(ids,np.einsum("ij,j->i",matrix,vector,optimize=False).tolist()))
