from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app.db.repository import connect, init_db, list_cases, list_sources
from app.ingestion.errors import IngestionError
from app.services import ingest_service
from app.services.source_service import (
    snapshot_local_source, release_snapshot, unused_snapshots, remove_unused_snapshots,
)

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation/fixtures"


@pytest.mark.parametrize("parallel", [False, True])
def test_identical_decks_share_bytes_but_not_source_or_case_identity(tmp_path, monkeypatch, parallel):
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    db = tmp_path / "cases.sqlite3"

    def ingest(_):
        return ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db)

    if parallel:
        with ThreadPoolExecutor(max_workers=2) as pool:
            imports = list(pool.map(ingest, range(2)))
    else:
        imports = [ingest(0), ingest(1)]
    assert len(list_sources(db)) == 2 and len(list_cases(db)) == 4
    assert imports[0]["source_id"] != imports[1]["source_id"]
    assert len(list((tmp_path / "uploads").glob("*.pptx"))) == 1
    assert unused_snapshots(db) == []
    monkeypatch.setattr(ingest_service, "parse_pptx", lambda path: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(IngestionError):
        ingest(2)
    assert Path(imports[0]["source"].source_snapshot_reference).is_file()
    assert len(list_cases(db)) == 4


def test_cleanup_checks_backups_and_active_snapshot_leases(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    ingest_service.ingest_deck(FIXTURES / "source_a.pptx", db)
    backups = tmp_path / "backups"
    backups.mkdir()
    with connect(db) as conn, closing(sqlite3.connect(backups / "retained.sqlite3")) as backup:
        conn.backup(backup)
    with connect(db) as conn:
        conn.execute("DELETE FROM field_metadata")
        conn.execute("DELETE FROM slides")
        conn.execute("DELETE FROM cases")
        conn.execute("DELETE FROM sources")
    active = snapshot_local_source(FIXTURES / "source_b.pptx", db, "b.pptx")
    orphan = tmp_path / "uploads/orphan.pptx"
    orphan.write_bytes(b"orphan")
    assert unused_snapshots(db) == [orphan]
    assert orphan.is_file()  # Preview never deletes.
    assert remove_unused_snapshots(db) == 1
    assert Path(active.source_snapshot_reference).is_file()
    assert len(list((tmp_path / "uploads").glob("*.pptx"))) == 2
    release_snapshot(active, db, committed=False)
    assert len(list((tmp_path / "uploads").glob("*.pptx"))) == 1
    assert unused_snapshots(db) == []
    (backups / "unreadable.sqlite3").write_bytes(b"not sqlite")
    with pytest.raises(RuntimeError, match="could not be checked"):
        remove_unused_snapshots(db)
