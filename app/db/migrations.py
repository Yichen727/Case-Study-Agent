"""Versioned, transactional SQLite initialization and legacy migration."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4
from app.models import REQUIRED_CASE_FIELDS, case_processing_status

SCHEMA_VERSION = 4
METADATA_FIELDS = REQUIRED_CASE_FIELDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL CHECK(source_type IN ('LOCAL', 'ONEDRIVE', 'SHAREPOINT')),
    filename TEXT NOT NULL,
    original_source_url TEXT,
    source_file_id TEXT,
    source_version TEXT,
    imported_at TEXT NOT NULL,
    ingestion_status TEXT NOT NULL,
    source_snapshot_reference TEXT,
    content_sha256 TEXT,
    requires_reimport INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    original_slide_id INTEGER,
    title TEXT NOT NULL,
    pillar TEXT, client TEXT, services TEXT,
    brief TEXT, creative TEXT, impact TEXT,
    source_deck TEXT NOT NULL,
    source_links_json TEXT NOT NULL DEFAULT '[]',
    source_link_labels_json TEXT NOT NULL DEFAULT '[]',
    linked_page_text TEXT,
    review_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS slides (
    slide_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    slide_number INTEGER NOT NULL,
    extracted_text TEXT NOT NULL,
    hyperlinks_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(case_id, slide_number)
);
CREATE TABLE IF NOT EXISTS metadata_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    old_value TEXT, new_value TEXT,
    source TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    previous_provenance_json TEXT,
    new_provenance_json TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS field_metadata (
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    value TEXT,
    source_kind TEXT NOT NULL,
    evidence_reference TEXT,
    verification_status TEXT NOT NULL,
    evidence_text TEXT,
    model TEXT,
    prompt_version TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(case_id, field_name)
);
"""

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    filename TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 1,
    result_json TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner, created_at);
CREATE TABLE IF NOT EXISTS web_cache (
    url TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS app_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    data_version INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO app_state(singleton, data_version) VALUES (1,0);
"""

REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_cache (
    cache_key TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ai_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    status TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hits INTEGER NOT NULL DEFAULT 0,
    applied_fields INTEGER NOT NULL DEFAULT 0,
    unknown_fields INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS case_reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL CHECK(action IN ('APPROVE','REOPEN')),
    verified_fields_json TEXT NOT NULL,
    unknown_fields_json TEXT NOT NULL,
    values_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_case ON case_reviews(case_id,review_id);
"""


def _backup_legacy_database(path: Path) -> None:
    folder = path.parent / "backups"
    folder.mkdir(exist_ok=True)
    backup_path = folder / f"{path.stem}.before-v{SCHEMA_VERSION}-{uuid4().hex[:8]}.sqlite3"
    # A separate reader avoids backing up a connection with a write transaction.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as reader:
        with closing(sqlite3.connect(backup_path)) as backup:
            reader.backup(backup)


def initialize_database(db_path: str | Path) -> bool:
    """Ensure schema exists; return whether it was created or migrated."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return False
        if version > SCHEMA_VERSION:
            raise RuntimeError("This database requires a newer application version.")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            # Another importer may have initialized while this one waited.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return False
            legacy = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cases'").fetchone()
            if legacy:
                _backup_legacy_database(path)
            # Fixed schema contains only simple CREATE statements, no triggers.
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            for table, names in {
                "field_metadata": ("evidence_text", "model", "prompt_version"),
                "metadata_history": ("previous_provenance_json", "new_provenance_json"),
            }.items():
                columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name in names:
                    if name not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")
            if legacy and version == 0:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
                additions = {
                    "source_id": "TEXT REFERENCES sources(source_id)",
                    "original_slide_id": "INTEGER",
                    "linked_page_text": "TEXT",
                    "source_link_labels_json": "TEXT NOT NULL DEFAULT '[]'",
                }
                for name, definition in additions.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE cases ADD COLUMN {name} {definition}")
                _migrate_legacy_cases(conn)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS case_source_slide ON cases(source_id, original_slide_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS cases_source ON cases(source_id)")
            for statement in (TASK_SCHEMA + REVIEW_SCHEMA).split(";"):
                if statement.strip():
                    conn.execute(statement)
            if refresh_case_statuses(conn):
                conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return True


def refresh_case_statuses(conn, case_ids=None):
    if case_ids is not None and not case_ids:
        return 0
    sql = f"SELECT case_id,review_status,{','.join(METADATA_FIELDS)} FROM cases"
    args = list(dict.fromkeys(case_ids)) if case_ids is not None else []
    if case_ids is not None:
        sql += " WHERE case_id IN (" + ",".join("?" for _ in args) + ")"
    changes = []
    for row in conn.execute(sql,args).fetchall():
        status = case_processing_status(dict(row))
        if row["review_status"] != status:
            changes.append((status,row["case_id"]))
    conn.executemany("UPDATE cases SET review_status=? WHERE case_id=?",changes)
    return len(changes)


def _migrate_legacy_cases(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT source_deck, MIN(created_at) AS imported_at FROM cases GROUP BY source_deck").fetchall():
        source_id = f"legacy-{uuid4().hex}"
        conn.execute(
            """INSERT INTO sources(source_id, source_type, filename, imported_at, ingestion_status, requires_reimport)
               VALUES (?, 'LOCAL', ?, ?, 'PARSED', 1)""",
            (source_id, row["source_deck"], row["imported_at"] or datetime.now(timezone.utc).isoformat()),
        )
        conn.execute("UPDATE cases SET source_id=? WHERE source_deck=?", (source_id, row["source_deck"]))
    conn.execute("""UPDATE cases SET review_status = CASE
                    WHEN lower(review_status)='approved' THEN 'APPROVED' ELSE 'NEEDS_REVIEW' END""")
    for row in conn.execute("SELECT * FROM cases").fetchall():
        values = {name: row[name] for name in METADATA_FIELDS}
        numbers = conn.execute("SELECT slide_number FROM slides WHERE case_id=? ORDER BY slide_number", (row["case_id"],)).fetchall()
        values.update(
            source_links=row["source_links_json"], original_slide_id=None,
            slide_number=",".join(str(item[0]) for item in numbers),
            linked_page_text=row["linked_page_text"],
        )
        for name, value in values.items():
            conn.execute(
                """INSERT INTO field_metadata(case_id, field_name, value, source_kind, verification_status)
                   VALUES (?, ?, ?, 'LEGACY_UNVERIFIED', 'UNVERIFIED')""",
                (row["case_id"], name, value),
            )
