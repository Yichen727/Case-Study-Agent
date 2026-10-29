from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.db.repository import init_db, list_cases, list_sources
from app.ingestion.errors import IngestionError
from app.ingestion import validation
from app.services import ingest_service

FIXTURE = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures" / "source_a.pptx"


def rewrite_package(target, removed=(), additions=None):
    with ZipFile(FIXTURE) as source, ZipFile(target, "w", ZIP_DEFLATED) as output:
        for entry in source.infolist():
            if entry.filename not in removed and entry.filename not in (additions or {}):
                output.writestr(entry, source.read(entry.filename))
        for name, data in (additions or {}).items():
            output.writestr(name, data)


@pytest.mark.parametrize("removed,addition", [
    (("[Content_Types].xml",), {}),
    ((), {"../outside.xml": b"<test/>"}),
    ((), {"untrusted.xml": b'<!DOCTYPE test [<!ENTITY x SYSTEM "file:///private">]><test>&x;</test>'}),
    ((), {"untrusted.xml": '<!DOCTYPE test [<!ENTITY x SYSTEM "file:///private">]><test>&x;</test>'.encode("utf-16")}),
])
def test_invalid_packages_stop_before_snapshot_and_records(tmp_path, removed, addition):
    path = tmp_path / "invalid.pptx"
    rewrite_package(path, removed, addition)
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    with pytest.raises(IngestionError) as error:
        ingest_service.ingest_deck(path, db)
    assert error.value.code == "INVALID_FILE"
    assert list_cases(db) == [] and list_sources(db) == []
    assert not list(tmp_path.glob("uploads/*"))


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_UNCOMPRESSED_BYTES", "MAX_PACKAGE_PARTS", "MAX_XML_BYTES"])
def test_package_resource_limits_are_enforced(monkeypatch, limit):
    monkeypatch.setattr(validation, limit, 1)
    with pytest.raises(IngestionError) as error:
        validation.validate_pptx(FIXTURE)
    assert error.value.code == "INVALID_FILE"


def test_valid_package_that_cannot_parse_has_distinct_error_and_no_records(tmp_path):
    path = tmp_path / "broken.pptx"
    rewrite_package(path, removed=("ppt/slides/slide1.xml",))
    validation.validate_pptx(path)
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    with pytest.raises(IngestionError) as error:
        ingest_service.ingest_deck(path, db)
    assert error.value.code == "PARSE_FAILED"
    assert list_cases(db) == [] and list_sources(db) == []
    assert not list(tmp_path.glob("uploads/*"))


def test_acquisition_error_is_safe(tmp_path):
    with pytest.raises(IngestionError) as error:
        ingest_service.ingest_deck(tmp_path / "private-internal-path.pptx", tmp_path / "cases.sqlite3")
    assert error.value.code == "FETCH_FAILED"
    assert "private-internal-path" not in str(error.value)
    assert "private-internal-path" not in error.value.user_message()


def test_acquired_cloud_bytes_use_same_pipeline_and_keep_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    ingest_service.ingest_deck(
        FIXTURE, db, source_type="SHAREPOINT",
        original_source_url="https://example.sharepoint.com/sites/demo/creds.pptx",
        source_file_id="test-file-id", source_version="test-version",
    )
    source = list_sources(db)[0]
    assert source["source_type"] == "SHAREPOINT"
    assert source["source_file_id"] == "test-file-id" and source["source_version"] == "test-version"
    assert source["original_source_url"].startswith("https://example.sharepoint.com/")
    assert len(list_cases(db)) == 2
