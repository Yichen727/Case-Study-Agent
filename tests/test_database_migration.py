from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app.db import migrations
from app.db.repository import connect, get_field_metadata, init_db, list_cases, list_sources, update_metadata
from app.services import ingest_service


def legacy_database(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY, title TEXT NOT NULL, pillar TEXT, client TEXT,
            services TEXT, brief TEXT, creative TEXT, impact TEXT, source_deck TEXT NOT NULL,
            source_links_json TEXT NOT NULL DEFAULT '[]', review_status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE slides (
            slide_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(case_id),
            slide_number INTEGER NOT NULL, extracted_text TEXT NOT NULL,
            hyperlinks_json TEXT NOT NULL DEFAULT '[]', UNIQUE(case_id, slide_number)
        );
        CREATE TABLE metadata_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL REFERENCES cases(case_id), field_name TEXT NOT NULL,
            old_value TEXT, new_value TEXT, source TEXT NOT NULL,
            verification_status TEXT NOT NULL, updated_by TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO cases(case_id, title, client, source_deck, review_status)
            VALUES ('slide-1', 'Legacy one', 'Manual client', 'old.pptx', 'confirmed'),
                   ('slide-2', 'Legacy two', NULL, 'old.pptx', 'approved');
        INSERT INTO slides(case_id, slide_number, extracted_text)
            VALUES ('slide-1', 1, 'Original text one'), ('slide-2', 2, 'Original text two');
        INSERT INTO metadata_history(case_id, field_name, new_value, source, verification_status, updated_by)
            VALUES ('slide-1', 'client', 'Manual client', 'user_entered', 'confirmed', 'legacy-editor');
        """)


def test_migration_backs_up_preserves_ids_text_and_history_and_runs_once(tmp_path):
    db = tmp_path / "cases.sqlite3"
    legacy_database(db)
    init_db(db)
    rows = {row["case_id"]: row for row in list_cases(db)}
    assert set(rows) == {"slide-1", "slide-2"}
    assert rows["slide-1"]["client"] == "Manual client"
    assert rows["slide-1"]["review_status"] == "NEEDS_REVIEW"
    assert rows["slide-2"]["review_status"] == "NEEDS_REVIEW"
    assert all(row["original_slide_id"] is None and row["requires_reimport"] for row in rows.values())
    assert len(list_sources(db)) == 1
    assert list_sources(db)[0]["source_snapshot_reference"] is None
    assert all(row["source_kind"] == "LEGACY_UNVERIFIED" for row in get_field_metadata(db, "slide-1"))
    with connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT extracted_text FROM slides WHERE case_id='slide-1'").fetchone()[0] == "Original text one"
        assert conn.execute("SELECT updated_by FROM metadata_history").fetchone()[0] == "legacy-editor"
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT review_status FROM cases WHERE case_id='slide-1'").fetchone()[0] == "confirmed"
    update_metadata(db, "slide-1", "client", "New manual client", "tester")
    init_db(db)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
    assert next(row for row in list_cases(db) if row["case_id"] == "slide-1")["client"] == "New manual client"


@pytest.mark.parametrize("failure_point", ["_backup_legacy_database", "_migrate_legacy_cases"])
def test_migration_failure_leaves_original_schema_and_values(tmp_path, monkeypatch, failure_point):
    db = tmp_path / "cases.sqlite3"
    legacy_database(db)

    def fail(*args):
        raise RuntimeError("Simulated migration failure")

    monkeypatch.setattr(migrations, failure_point, fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        init_db(db)
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 2
        assert conn.execute("SELECT client FROM cases WHERE case_id='slide-1'").fetchone()[0] == "Manual client"
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='sources'").fetchone() is None


def test_concurrent_imports_migrate_legacy_database_once(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    legacy_database(db)
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    fixtures = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures"
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda filename: ingest_service.ingest_deck(fixtures / filename, db),
                          ("source_a.pptx", "source_b.pptx")))
    assert len(list_cases(db)) == 5
    assert len(list_sources(db)) == 3
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_version_one_upgrade_preserves_existing_sources_manual_values_and_history(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    fixture = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
    result = ingest_service.ingest_deck(fixture, db)
    case_id = result["cases"][0].case_id
    update_metadata(db, case_id, "client", "Manual client", "reviewer", verification_status="confirmed")
    before_cases, before_sources = list_cases(db), list_sources(db)
    with connect(db) as conn:
        before_fields = [tuple(row) for row in conn.execute("SELECT * FROM field_metadata ORDER BY case_id,field_name")]
        before_history = [tuple(row) for row in conn.execute("SELECT * FROM metadata_history")]
        conn.execute("DROP TABLE tasks")
        conn.execute("DROP TABLE web_cache")
        conn.execute("DROP TABLE app_state")
        conn.execute("PRAGMA user_version=1")
    assert init_db(db)
    assert list_cases(db) == before_cases and list_sources(db) == before_sources
    with connect(db) as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM field_metadata ORDER BY case_id,field_name")] == before_fields
        assert [tuple(row) for row in conn.execute("SELECT * FROM metadata_history")] == before_history
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not init_db(db)
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1


def test_version_two_upgrade_adds_ai_and_reviews_without_changing_old_columns(tmp_path, monkeypatch):
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr(ingest_service,"fetch_case_page",lambda url, **kwargs: {})
    fixture = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
    imported = ingest_service.ingest_deck(fixture,db)
    update_metadata(db,imported["cases"][0].case_id,"client","Human client","reviewer")
    with connect(db) as conn:
        for name in ("ai_runs","ai_cache","case_reviews"):
            conn.execute(f"DROP TABLE {name}")
        for table,names in {"field_metadata":("evidence_text","model","prompt_version"),
                            "metadata_history":("previous_provenance_json","new_provenance_json")}.items():
            for name in names:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
        conn.execute("PRAGMA user_version=2")
        columns = {name:[row["name"] for row in conn.execute(f"PRAGMA table_info({name})")]
                   for name in ("cases","sources","slides","field_metadata","metadata_history")}
        before = {name:[tuple(row) for row in conn.execute(f"SELECT {','.join(names)} FROM {name} ORDER BY rowid")]
                  for name,names in columns.items()}
    assert init_db(db)
    with connect(db) as conn:
        after = {name:[tuple(row) for row in conn.execute(f"SELECT {','.join(names)} FROM {name} ORDER BY rowid")]
                 for name,names in columns.items()}
        assert before == after
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0] == 0
    assert not init_db(db)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_version_three_recalculates_status_and_preserves_metadata_and_old_approval_history(tmp_path, monkeypatch):
    import json
    from app.db.repository import data_version, update_case_metadata
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr(ingest_service,"fetch_case_page",lambda url, **kwargs: {})
    fixture = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
    imported = ingest_service.ingest_deck(fixture,db)
    complete,incomplete = [case.case_id for case in imported["cases"]]
    update_case_metadata(db,complete,{"client":"Client","services":"Filming"})
    update_case_metadata(db,incomplete,{"client":"Client","services":"Filming","brief":""})
    with connect(db) as conn:
        conn.execute("UPDATE cases SET review_status='NEEDS_REVIEW' WHERE case_id=?",(complete,))
        conn.execute("UPDATE cases SET review_status='APPROVED' WHERE case_id=?",(incomplete,))
        conn.execute("""INSERT INTO case_reviews(case_id,reviewer,action,verified_fields_json,unknown_fields_json,values_json)
                        VALUES (?,'old-reviewer','APPROVE','[]',?,'{}')""",(incomplete,json.dumps(["brief"])))
        conn.execute("PRAGMA user_version=3")
        names = ("sources","slides","field_metadata","metadata_history","case_reviews")
        before = {name:[tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid")] for name in names}
    old_version = data_version(db)
    assert init_db(db)
    rows = {row["case_id"]:row for row in list_cases(db)}
    assert rows[complete]["review_status"] == "CONFIRMED"
    assert rows[incomplete]["review_status"] == "NEEDS_REVIEW" and rows[incomplete]["brief"] is None
    with connect(db) as conn:
        assert before == {name:[tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY rowid")] for name in names}
    assert data_version(db) == old_version + 1
    assert not init_db(db)
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1
