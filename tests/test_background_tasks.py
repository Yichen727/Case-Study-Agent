from io import BytesIO
import json
from pathlib import Path
from threading import Event

import pytest

from app.db.repository import connect, list_cases, list_sources, update_metadata
from app.search.keyword_search import search_cases
from app.services import ingest_service
from app.services.task_service import TaskManager, TaskQueueFull

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation/fixtures"


def test_queue_is_bounded_and_cases_are_available_before_web_finishes(tmp_path, monkeypatch):
    entered, release = Event(), Event()

    def slow_page(url, **kwargs):
        entered.set()
        assert release.wait(10)
        return {"text": "Evidence", "client": "Web client", "services": "Web services"}

    monkeypatch.setattr(ingest_service, "fetch_case_page", slow_page)
    db = tmp_path / "cases.sqlite3"
    manager = TaskManager(db, capacity=2)
    try:
        first = manager.submit_import(FIXTURES / "source_a.pptx", "a.pptx")
        assert entered.wait(5)
        rows = list_cases(db)
        assert len(rows) == 2
        assert len(search_cases(db, "SYNTHETIC: SAME NAME")) == 2
        assert manager.get_task(first, "another-user") is None
        target = rows[0]["case_id"]
        update_metadata(db, target, "client", "Human client", "reviewer")
        update_metadata(db, target, "services", "", "reviewer")
        second = manager.submit_import(FIXTURES / "source_b.pptx", "b.pptx")
        unread = BytesIO(b"must not read when full")
        with pytest.raises(TaskQueueFull):
            manager.submit_import(unread, "c.pptx")
        assert unread.tell() == 0
        assert manager.get_task(second)["status"] == "QUEUED"
        release.set()
        assert manager.wait(first)["status"] == "SUCCEEDED"
        assert manager.wait(second)["status"] == "SUCCEEDED"
        row = next(row for row in list_cases(db) if row["case_id"] == target)
        assert row["client"] == "Human client" and row["services"] is None
        assert len(list_cases(db)) == 3
        assert list((tmp_path / "incoming").glob("*.pptx")) == []
    finally:
        release.set()
        manager.shutdown()


def test_retry_after_commit_is_idempotent_and_failed_input_is_retained(tmp_path, monkeypatch):
    def fail_web(url, **kwargs):
        raise RuntimeError("private detail must not enter task state")

    monkeypatch.setattr(ingest_service, "fetch_case_page", fail_web)
    db = tmp_path / "cases.sqlite3"
    manager = TaskManager(db)
    try:
        task_id = manager.submit_import(FIXTURES / "source_a.pptx", "a.pptx")
        failed = manager.wait(task_id)
        assert failed["status"] == "FAILED" and failed["error_code"] == "TASK_FAILED"
        assert "private detail" not in json.dumps(failed)
        assert len(list_cases(db)) == 2
        assert len(list((tmp_path / "incoming").glob("*.pptx"))) == 1
        case_id = list_cases(db)[0]["case_id"]
        update_metadata(db, case_id, "client", "Keep me", "reviewer")
        assert not manager.retry(task_id, "another-user")
        monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
        assert manager.retry(task_id)
        assert manager.wait(task_id)["status"] == "SUCCEEDED"
        assert len(list_cases(db)) == 2 and len(list_sources(db)) == 1
        assert next(row for row in list_cases(db) if row["case_id"] == case_id)["client"] == "Keep me"
        assert not manager.retry(task_id)
        assert manager.clear_finished() == 1
        assert not manager.list_tasks("local")
    finally:
        manager.shutdown()


def test_restart_marks_persisted_tasks_interrupted_and_requires_explicit_retry(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    manager = TaskManager(db)
    manager.shutdown()
    folder = tmp_path / "incoming"
    folder.mkdir()
    upload = folder / "interrupted.pptx"
    upload.write_bytes((FIXTURES / "source_a.pptx").read_bytes())
    with connect(db) as conn:
        conn.execute("""INSERT INTO tasks(task_id,owner,kind,filename,payload_json,status,stage)
                        VALUES ('interrupted','local','IMPORT','a.pptx',?,'RUNNING','Parsing')""",
                     (json.dumps({"input_path": str(upload), "source_id": "interrupted", "refresh_pages": False}),))
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    manager = TaskManager(db)
    try:
        assert manager.get_task("interrupted")["status"] == "INTERRUPTED"
        assert list_cases(db) == [] and upload.is_file()
        assert manager.retry("interrupted")
        assert manager.wait("interrupted")["status"] == "SUCCEEDED"
        assert {row["source_id"] for row in list_cases(db)} == {"interrupted"}
    finally:
        manager.shutdown()


def test_export_uses_the_same_worker(tmp_path):
    from app.export.openxml_export import SlideReference
    from pptx import Presentation

    manager = TaskManager(tmp_path / "cases.sqlite3")
    try:
        task_id = manager.submit_export([SlideReference(FIXTURES / "source_b.pptx", 256)])
        task = manager.wait(task_id)
        assert task["status"] == "SUCCEEDED"
        output = Path(json.loads(task["result_json"])["output_path"])
        assert len(Presentation(output).slides) == 1
    finally:
        manager.shutdown()
