from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path

import pytest
from app.ingestion.errors import IngestionError

from app.db.repository import (
    connect, get_field_metadata, init_db, list_cases, list_sources, save_cases, update_metadata,
)
from app.ingestion.pptx_parser import parse_pptx
from app.services import ingest_service

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures"


@pytest.fixture(autouse=True)
def offline_pages(monkeypatch):
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {
        "text": None, "client": None, "services": None,
    })


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("same_filename", [False, True])
def test_imports_isolate_identical_slide_ids_and_page_numbers(tmp_path, parallel, same_filename):
    db = tmp_path / "cases.sqlite3"

    def ingest(filename):
        return ingest_service.ingest_deck(
            FIXTURES / filename, db, source_deck="same-name.pptx" if same_filename else filename
        )

    if parallel:
        with ThreadPoolExecutor(max_workers=2) as executor:
            imports = list(executor.map(ingest, ("source_a.pptx", "source_b.pptx")))
    else:
        imports = [ingest("source_a.pptx"), ingest("source_b.pptx")]
    rows = list_cases(db)
    assert len(rows) == 3
    assert len({row["case_id"] for row in rows}) == 3
    assert len(list_sources(db)) == 2
    assert imports[0]["source_id"] != imports[1]["source_id"]
    first_pages = [row for row in rows if row["slide_numbers"] == "1"]
    assert len(first_pages) == 2
    assert first_pages[0]["original_slide_id"] == first_pages[1]["original_slide_id"]
    assert first_pages[0]["source_id"] != first_pages[1]["source_id"]
    assert all(row["review_status"] == "NEEDS_REVIEW" for row in rows)
    for result in imports:
        snapshot = Path(result["source"].source_snapshot_reference)
        original = FIXTURES / ("source_a.pptx" if result is imports[0] else "source_b.pptx")
        assert snapshot.read_bytes() == original.read_bytes()
        assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == result["source"].content_sha256


def test_snapshot_survives_removal_of_temporary_upload(tmp_path):
    upload = tmp_path / "upload.pptx"
    upload.write_bytes((FIXTURES / "source_a.pptx").read_bytes())
    result = ingest_service.ingest_deck(upload, tmp_path / "cases.sqlite3")
    upload.unlink()
    assert len(parse_pptx(result["source"].source_snapshot_reference)) == 2


def test_same_deck_reupload_reuses_import_and_preserves_manual_metadata(tmp_path,monkeypatch):
    db = tmp_path / "cases.sqlite3"
    first = ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db)
    second = ingest_service.ingest_deck(FIXTURES / "source_b.pptx", db)
    case_id = first["cases"][0].case_id
    update_metadata(db, case_id, "client", "Only source A", "tester", verification_status="confirmed")
    save_cases(db, first["cases"], {s.slide_number: s.text for s in first["slides"]}, source=first["source"])
    monkeypatch.setattr(ingest_service,"parse_pptx",lambda path:pytest.fail("Repeated deck must not be parsed"))
    monkeypatch.setattr(ingest_service,"enrich_source",lambda *args,**kwargs:pytest.fail("Repeated deck must not fetch web metadata"))
    monkeypatch.setattr(ingest_service,"infer_missing_metadata",lambda *args,**kwargs:pytest.fail("Repeated deck must not infer metadata"))
    third = ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db,source_deck="renamed.pptx")
    rows = {row["case_id"]: row for row in list_cases(db)}
    assert len(rows) == 3 and len(list_sources(db)) == 2
    assert third["reused"] and third["source_id"] == first["source_id"]
    assert third["case_count"] == 2
    assert rows[case_id]["client"] == "Only source A"
    assert rows[case_id]["review_status"] == "NEEDS_REVIEW"
    assert rows[second["cases"][0].case_id]["client"] is None
    provenance = {item["field_name"]: item for item in get_field_metadata(db, case_id)}
    assert provenance["client"]["source_kind"] == "HUMAN_ENTERED"
    assert provenance["client"]["verification_status"] == "VERIFIED"
    assert provenance["title"]["source_kind"] == "SLIDE_EXTRACTED"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metadata_history WHERE case_id=?", (case_id,)).fetchone()[0] == 1


def test_web_fields_record_their_actual_page_without_auto_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {
        "text": "Test page", "client": "Synthetic client", "services": "Synthetic services",
    })
    db = tmp_path / "cases.sqlite3"
    result = ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db)
    case = result["cases"][0]
    fields = {row["field_name"]: row for row in get_field_metadata(db, case.case_id)}
    assert fields["client"]["source_kind"] == "LINK_EXTRACTED"
    assert fields["services"]["evidence_reference"] == "https://example.com/synthetic-fixture"
    rows = list_cases(db)
    assert next(row for row in rows if row["slide_numbers"] == "1")["review_status"] == "CONFIRMED"
    assert next(row for row in rows if row["slide_numbers"] == "2")["review_status"] == "NEEDS_REVIEW"


def test_invalid_file_creates_no_source_cases_or_snapshot(tmp_path):
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    invalid = tmp_path / "invalid.pptx"
    invalid.write_bytes(b"not a presentation")
    with pytest.raises(IngestionError) as error:
        ingest_service.ingest_deck(invalid, db)
    assert error.value.code == "INVALID_FILE"
    assert list_sources(db) == []
    assert list_cases(db) == []
    assert list(tmp_path.glob("uploads/*")) == []


def test_failed_case_write_rolls_back_whole_import_and_removes_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"

    def fail_after_first_case(db_path, cases, slide_text_by_number, *, source):
        save_cases(db_path, cases, {1: slide_text_by_number[1]}, source=source)

    monkeypatch.setattr(ingest_service, "save_cases", fail_after_first_case)
    with pytest.raises(IngestionError) as error:
        ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db)
    assert error.value.code == "PARSE_FAILED"
    assert isinstance(error.value.__cause__, KeyError)
    assert list_sources(db) == []
    assert list_cases(db) == []
    assert list((tmp_path / "uploads").iterdir()) == []
