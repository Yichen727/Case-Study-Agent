"""Deterministic search documents and synchronous, local FTS maintenance."""
import hashlib
import re

INDEX_VERSION = "case-search-v1"
EMBEDDING_DIMENSIONS = 768
MAX_INPUT_BYTES = 6000  # Conservative bound below the model's 8192-token limit.


def bounded_text(text):
    return text.encode("utf-8")[:MAX_INPUT_BYTES].decode("utf-8",errors="ignore")


def text_hash(text):
    return hashlib.sha256((INDEX_VERSION + "\n" + text).encode()).hexdigest()


def make_document(case, original):
    fields = ("title","pillar","client","services","brief","creative","impact")
    parts = [f"{name.title()}: {case[name]}" for name in fields if case.get(name)]
    # Nonstandard slides still need their full original narrative in the index.
    if not all(case.get(name) for name in ("brief","creative","impact")):
        lines = [line for line in original.splitlines() if not line.strip().isdigit()
                 and not line.upper().startswith(("REPLACE WITH", "READ MORE", "WATCH VIDEO"))]
        parts.append("Original slide: " + "\n".join(lines))
    return bounded_text(re.sub(r"\s+"," ","\n".join(parts)).strip())


def sync_documents(conn, case_ids=None):
    if case_ids is not None and not case_ids:
        return 0
    sql = """SELECT c.*,GROUP_CONCAT(s.extracted_text,char(10)) AS original_text
             FROM cases c LEFT JOIN slides s ON s.case_id=c.case_id"""
    params = list(dict.fromkeys(case_ids)) if case_ids is not None else []
    if case_ids is not None:
        sql += " WHERE c.case_id IN (" + ",".join("?" for _ in params) + ")"
    sql += " GROUP BY c.case_id"
    changed = 0
    for row in conn.execute(sql,params).fetchall():
        case = dict(row)
        original = case["original_text"] or ""
        document = make_document(case,original)
        fingerprint = text_hash(document)
        previous = conn.execute("SELECT content_hash,original_text FROM search_documents WHERE case_id=?",(case["case_id"],)).fetchone()
        if previous and previous[0] == fingerprint and previous[1] == original:
            continue
        conn.execute("""INSERT INTO search_documents(case_id,content_hash,document_text,original_text) VALUES (?,?,?,?)
                        ON CONFLICT(case_id) DO UPDATE SET content_hash=excluded.content_hash,
                        document_text=excluded.document_text,original_text=excluded.original_text""",
                     (case["case_id"],fingerprint,document,original))
        conn.execute("DELETE FROM cases_fts WHERE case_id=?",(case["case_id"],))
        conn.execute("INSERT INTO cases_fts(case_id,title,pillar,client,services,body) VALUES (?,?,?,?,?,?)",
                     (case["case_id"],case["title"],case["pillar"],case["client"],case["services"],document))
        changed += 1
    return changed
