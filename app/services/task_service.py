"""One bounded worker for the local single-user workflow; no Streamlit calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import logging
from pathlib import Path
from threading import BoundedSemaphore, RLock
from uuid import uuid4

from app.db.repository import connect, init_db, data_version
from app import config
from app.search.embedding_service import build_index, index_status
from app.export.openxml_export import OriginalSlideExportError
from app.export.slide_export import prepare_export, export_original_slides, CaseExportError
from app.ingestion.errors import IngestionError
from app.ingestion.validation import MAX_FILE_BYTES
from app.services.enrichment_service import enrich_source
from app.services.ingest_service import ingest_deck
from app.services.ai_enrichment import infer_missing_metadata
from app.generation.models import Draft
from app.generation.storage import DraftStore, DraftChangedError, DraftStorageError
from app.services.case_generation import start_case, complete_case, retry_case, GenerationError
from app.export.generated_case_export import prepare_generated_export, export_generated_case, GeneratedExportError


class TaskQueueFull(RuntimeError):
    pass


class TaskManager:
    def __init__(self, db_path, capacity=4):
        self.db_path = Path(db_path).resolve()
        self.drafts = DraftStore(self.db_path.parent)
        init_db(self.db_path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="case-study")
        self._capacity = BoundedSemaphore(capacity)
        self._lock = RLock()
        self._futures = {}
        self._index_failure = None
        self.closed = False
        with connect(self.db_path) as conn:
            conn.execute("UPDATE ai_runs SET status='INTERRUPTED',error_code='INTERRUPTED',finished_at=CURRENT_TIMESTAMP WHERE status='RUNNING'")
            conn.execute("""UPDATE tasks SET status='INTERRUPTED',stage='Interrupted; retry to continue',
                            error_code='INTERRUPTED',updated_at=CURRENT_TIMESTAMP
                            WHERE status IN ('QUEUED','RUNNING')""")
        # Only successful task input copies are disposable without losing retry.
        for task in self.list_tasks(None, limit=None):
            if task["status"] == "SUCCEEDED":
                self._remove_input(task)

    def _reserve(self):
        if self.closed:
            raise RuntimeError("The task runner is stopped. Restart the app.")
        if not self._capacity.acquire(blocking=False):
            raise TaskQueueFull("The task queue is full. Wait for an import to finish, then try again.")

    def submit_import(self, upload, filename, owner="local"):
        self._reserve()
        task_id = uuid4().hex
        folder = self.db_path.parent / "incoming"
        path = folder / f"{task_id}.pptx"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            reader_context = Path(upload).open("rb") if isinstance(upload, (str, Path)) else nullcontext(upload)
            with reader_context as reader, path.open("xb") as writer:
                reader.seek(0)
                size = 0
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise IngestionError("INVALID_FILE")
                    writer.write(chunk)
            payload = {"input_path": str(path), "source_id": task_id, "refresh_pages": False}
            self._create(task_id, "IMPORT", filename, owner, payload)
        except Exception:
            path.unlink(missing_ok=True)
            self._capacity.release()
            raise
        return task_id

    def submit_enrichment(self, source_id, filename, owner="local", refresh=True):
        self._reserve()
        task_id = uuid4().hex
        try:
            self._create(task_id, "ENRICH", filename, owner, {"source_id": source_id, "refresh_pages": refresh})
        except Exception:
            self._capacity.release()
            raise
        return task_id

    def submit_export(self, case_ids, owner="local"):
        plan = prepare_export(self.db_path,tuple(case_ids))
        with self._lock:
            with connect(self.db_path) as conn:
                candidates = conn.execute("""SELECT * FROM tasks WHERE owner=? AND kind='EXPORT' AND cache_key=?
                                             AND status IN ('QUEUED','RUNNING','SUCCEEDED')
                                             ORDER BY created_at DESC,rowid DESC""",(owner,plan["cache_key"])).fetchall()
            for task in candidates:
                if task["status"] in {"QUEUED","RUNNING"}:
                    return task["task_id"]
                output = self.db_path.parent / "exports" / f"{plan['cache_key']}.pptx"
                if output.is_file() and output.stat().st_size:
                    return task["task_id"]
            self._reserve()
            task_id = uuid4().hex
            try:
                self._create(task_id,"EXPORT","Selected cases",owner,plan,cache_key=plan["cache_key"])
            except Exception:
                self._capacity.release()
                raise
            return task_id

    def submit_ai_enrichment(self, source_id, filename, owner="local"):
        self._reserve()
        task_id = uuid4().hex
        try:
            self._create(task_id,"AI_ENRICH",filename,owner,{"source_id":source_id})
        except Exception:
            self._capacity.release()
            raise
        return task_id

    def case_task(self, draft_id, owner="local", *, kind=None, active_only=False):
        with connect(self.db_path) as conn:
            sql = """SELECT * FROM tasks WHERE owner=? AND kind IN ('CASE_GENERATE','CASE_EXPORT')
                     AND json_extract(payload_json,'$.draft_id')=?"""
            args = [owner, draft_id]
            if kind:
                sql += " AND kind=?"
                args.append(kind)
            if active_only:
                sql += " AND status IN ('QUEUED','RUNNING')"
            row = conn.execute(sql + " ORDER BY created_at DESC,rowid DESC LIMIT 1", args).fetchone()
            return dict(row) if row else None

    def active_case_drafts(self, owner="local"):
        with connect(self.db_path) as conn:
            return {row[0] for row in conn.execute(
                """SELECT DISTINCT json_extract(payload_json,'$.draft_id') FROM tasks
                   WHERE owner=? AND kind IN ('CASE_GENERATE','CASE_EXPORT')
                   AND status IN ('QUEUED','RUNNING')""", (owner,))}

    def submit_case_generation(self, draft_id, details=None, owner="local", *, retry=False):
        with self._lock:
            active = self.case_task(draft_id, owner, kind="CASE_GENERATE", active_only=True)
            if active:
                return active["task_id"]
            draft = self.drafts.load(draft_id)
            if retry and not draft.request_count:
                raise GenerationError("GENERATION_DRAFT_NOT_READY")
            if draft.request_count and details is None and not retry:
                raise GenerationError("GENERATION_INPUT_INVALID")
            self._reserve()
            task_id = uuid4().hex
            try:
                self._create(task_id, "CASE_GENERATE", "New case draft", owner,
                             {"draft_id": draft_id, "base": draft.model_dump(), "details": details, "retry": retry})
            except Exception:
                self._capacity.release()
                raise
            return task_id

    def skip_case_details(self, draft_id, owner="local"):
        with self._lock:
            if self.case_task(draft_id, owner, kind="CASE_GENERATE", active_only=True):
                raise TaskQueueFull("Wait for the current draft task before skipping.")
            draft = self.drafts.load(draft_id)
            return self.drafts.commit_generated(draft, complete_case(draft))

    def delete_case_draft(self, draft_id):
        with self._lock:
            with connect(self.db_path) as conn:
                if conn.execute("""SELECT 1 FROM tasks WHERE kind IN ('CASE_GENERATE','CASE_EXPORT')
                                   AND json_extract(payload_json,'$.draft_id')=?
                                   AND status IN ('QUEUED','RUNNING') LIMIT 1""", (draft_id,)).fetchone():
                    raise TaskQueueFull("This draft is being processed. Wait for its tasks to finish before deleting it.")
            self.drafts.delete(draft_id)
            with connect(self.db_path) as conn:
                conn.execute("""DELETE FROM tasks WHERE kind IN ('CASE_GENERATE','CASE_EXPORT')
                                AND json_extract(payload_json,'$.draft_id')=?""", (draft_id,))

    def submit_case_export(self, draft_id, owner="local"):
        with self._lock:
            plan = prepare_generated_export(self.drafts.load(draft_id))
            with connect(self.db_path) as conn:
                rows = conn.execute("""SELECT * FROM tasks WHERE owner=? AND kind='CASE_EXPORT' AND cache_key=?
                                       AND status IN ('QUEUED','RUNNING','SUCCEEDED') ORDER BY rowid DESC""",
                                    (owner, plan["cache_key"])).fetchall()
            for task in rows:
                if task["status"] != "SUCCEEDED" or Path(json.loads(task["result_json"])["output_path"]).is_file():
                    return task["task_id"]
            self._reserve()
            task_id = uuid4().hex
            try:
                self._create(task_id, "CASE_EXPORT", "New case slide", owner, plan, cache_key=plan["cache_key"])
            except Exception:
                self._capacity.release()
                raise
            return task_id

    def ensure_indexing(self, owner="local", *, force=False):
        settings = config.get_ai_settings()
        if not settings.api_key or not index_status(self.db_path)["pending"]:
            return None
        version = (settings.embedding_model,data_version(self.db_path))
        if not force and self._index_failure == version:
            return None
        with self._lock:
            with connect(self.db_path) as conn:
                if conn.execute("SELECT 1 FROM tasks WHERE kind='INDEX' AND status IN ('QUEUED','RUNNING')").fetchone():
                    return None
            try:
                self._reserve()
            except TaskQueueFull:
                return None
            task_id = uuid4().hex
            try:
                self._create(task_id,"INDEX","Search index",owner,{})
            except Exception:
                self._capacity.release()
                raise
        return task_id

    def _build_index(self, source_id=None, progress=None):
        result = build_index(self.db_path,source_id,progress=progress)
        if result["status"] in {"FAILED","PARTIAL"}:
            self._index_failure = (result["model"],data_version(self.db_path))
        else:
            self._index_failure = None
        return result

    def _create(self, task_id, kind, filename, owner, payload, cache_key=None):
        with connect(self.db_path) as conn:
            conn.execute("""INSERT INTO tasks(task_id,owner,kind,filename,payload_json,cache_key,status,stage)
                            VALUES (?,?,?,?,?,?,'QUEUED','Queued')""",
                         (task_id,owner,kind,filename,json.dumps(payload),cache_key))
        self._enqueue(task_id)

    def _enqueue(self, task_id):
        with self._lock:
            future = self._executor.submit(self._run, task_id)
            self._futures[task_id] = future

            def finished(_):
                with self._lock:
                    self._futures.pop(task_id, None)
                self._capacity.release()

            future.add_done_callback(finished)

    def _update(self, task_id, **values):
        allowed = {"status", "stage", "completed", "total", "result_json", "error_code"}
        if not set(values).issubset(allowed):
            raise ValueError("Unsupported task field")
        with connect(self.db_path) as conn:
            conn.execute("UPDATE tasks SET " + ",".join(f"{name}=?" for name in values)
                         + ",updated_at=CURRENT_TIMESTAMP WHERE task_id=?", (*values.values(),task_id))

    def _run(self, task_id):
        task = self.get_task(task_id)
        payload = json.loads(task["payload_json"])
        self._update(task_id, status="RUNNING", stage="Starting", error_code=None)

        def progress(stage, completed, total):
            self._update(task_id, stage=stage, completed=completed, total=max(1,total))

        try:
            if task["kind"] == "IMPORT":
                result = ingest_deck(payload["input_path"], self.db_path, source_deck=task["filename"],
                                     source_id=payload["source_id"], progress=progress,
                                     refresh_pages=payload["refresh_pages"])
                summary = {key: result[key] for key in ("source_id", "slide_count", "case_count", "enrichment", "ai", "reused")}
                summary["index"] = self._build_index(result["source_id"],progress)
            elif task["kind"] == "ENRICH":
                result = enrich_source(payload["source_id"], self.db_path, progress=progress,
                                       refresh=payload["refresh_pages"])
                result.pop("applied")
                summary = {"source_id": payload["source_id"], "enrichment": result}
                summary["index"] = self._build_index(payload["source_id"],progress)
            elif task["kind"] == "AI_ENRICH":
                summary = {"source_id":payload["source_id"],
                           "ai":infer_missing_metadata(payload["source_id"],self.db_path,progress=progress)}
                summary["index"] = self._build_index(payload["source_id"],progress)
            elif task["kind"] == "INDEX":
                summary = {"index":self._build_index(progress=progress)}
            elif task["kind"] == "EXPORT":
                output = self.db_path.parent / "exports" / f"{payload['cache_key']}.pptx"
                export_original_slides(payload,output,progress=progress)
                summary = {"output_path":str(output),"slide_count":payload["slide_count"],"cache_key":payload["cache_key"]}
            elif task["kind"] == "CASE_GENERATE":
                base = Draft.model_validate(payload["base"])
                current = self.drafts.load(base.draft_id)
                if (current.request_count != base.request_count
                        or current.original_description != base.original_description):
                    raise DraftChangedError()
                progress("Writing your case draft", 0, 1)
                if payload.get("retry"):
                    result = retry_case(base)
                else:
                    result = start_case(base.original_description) if not base.request_count else complete_case(base, payload["details"])
                saved = self.drafts.commit_generated(base, result)
                summary = {"draft_id": saved.draft_id, "revision": saved.revision, "body_words": saved.content.body_words}
            elif task["kind"] == "CASE_EXPORT":
                progress("Preparing your single case slide", 0, 1)
                summary = export_generated_case(payload, self.drafts)
            else:
                raise ValueError("Unsupported task kind")
            self._update(task_id, status="SUCCEEDED", stage="Complete", completed=1, total=1,
                         result_json=json.dumps(summary), error_code=None)
            self._remove_input(task)
        except Exception as exc:
            code = exc.code if isinstance(exc,(IngestionError,CaseExportError,OriginalSlideExportError,
                                               GenerationError,DraftStorageError,DraftChangedError,GeneratedExportError)) else "TASK_FAILED"
            logging.getLogger(__name__).warning("Background task failed: %s", code)
            self._update(task_id, status="FAILED", stage="Could not complete this task", error_code=code)

    def get_task(self, task_id, owner=None):
        with connect(self.db_path) as conn:
            sql = "SELECT * FROM tasks WHERE task_id=?"
            args = [task_id]
            if owner is not None:
                sql += " AND owner=?"
                args.append(owner)
            row = conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def list_tasks(self, owner, limit=20):
        with connect(self.db_path) as conn:
            sql = "SELECT * FROM tasks"
            args = []
            if owner is not None:
                sql += " WHERE owner=?"
                args.append(owner)
            sql += " ORDER BY created_at DESC,rowid DESC"
            if limit is not None:
                sql += " LIMIT ?"
                args.append(limit)
            return [dict(row) for row in conn.execute(sql,args).fetchall()]

    def retry(self, task_id, owner="local"):
        with self._lock:
            task = self.get_task(task_id, owner)
            if not task or task["status"] not in {"FAILED", "INTERRUPTED"}:
                return False
            payload = json.loads(task["payload_json"])
            if task["kind"] == "IMPORT" and not Path(payload["input_path"]).is_file():
                return False
            self._reserve()
            with connect(self.db_path) as conn:
                updated = conn.execute("""UPDATE tasks SET status='QUEUED',stage='Queued',error_code=NULL,
                                         payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE task_id=? AND owner=?
                                         AND status IN ('FAILED','INTERRUPTED')""",
                                       (json.dumps({**payload,"refresh_pages":True}),task_id,owner))
            if not updated.rowcount:
                self._capacity.release()
                return False
            self._enqueue(task_id)
            return True

    def _remove_input(self, task):
        payload = json.loads(task["payload_json"])
        if "input_path" in payload:
            path = Path(payload["input_path"]).resolve()
            if path.parent != self.db_path.parent / "incoming":
                raise ValueError("Task input leaves the managed folder")
            path.unlink(missing_ok=True)

    def clear_finished(self, owner="local"):
        with self._lock:
            removed = 0
            for task in self.list_tasks(owner, limit=None):
                if task["status"] not in {"QUEUED", "RUNNING"}:
                    with connect(self.db_path) as conn:
                        cursor = conn.execute("DELETE FROM tasks WHERE task_id=? AND owner=? AND status NOT IN ('QUEUED','RUNNING')",
                                              (task["task_id"],owner))
                    if cursor.rowcount:
                        self._remove_input(task)
                        self._remove_export(task)
                        removed += 1
            return removed

    def _remove_export(self, task):
        if task["kind"]!="EXPORT":
            return
        with connect(self.db_path) as conn:
            if task["cache_key"] and conn.execute("SELECT 1 FROM tasks WHERE kind='EXPORT' AND cache_key=? LIMIT 1",
                                                  (task["cache_key"],)).fetchone():
                return
        result = json.loads(task["result_json"] or "{}")
        if result.get("output_path"):
            output = Path(result["output_path"]).resolve()
            if output.parent!=self.db_path.parent / "exports":
                raise ValueError("Task output leaves the managed folder")
            output.unlink(missing_ok=True)

    def wait(self, task_id, owner="local", timeout=30):
        """CLI/tests only: the web UI polls and never waits for a Future."""
        if self.get_task(task_id,owner) is None:
            return None
        with self._lock:
            future = self._futures.get(task_id)
        if future:
            future.result(timeout=timeout)
        return self.get_task(task_id,owner)

    def shutdown(self):
        self.closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


_managers = {}
_manager_lock = RLock()


def get_task_manager(db_path):
    key = str(Path(db_path).resolve())
    with _manager_lock:
        if key not in _managers or _managers[key].closed:
            _managers[key] = TaskManager(key)
        return _managers[key]
