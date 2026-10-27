from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from app.models import ParsedCase


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    pillar TEXT,
    client TEXT,
    services TEXT,
    brief TEXT,
    creative TEXT,
    impact TEXT,
    source_deck TEXT NOT NULL,
    source_links_json TEXT NOT NULL DEFAULT '[]',
    source_link_labels_json TEXT NOT NULL DEFAULT '[]',
    linked_page_text TEXT,
    review_status TEXT NOT NULL DEFAULT 'needs_review',
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
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        # Lightweight migration for databases created by earlier scaffold versions.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(cases)")}
        if "linked_page_text" not in columns:
            conn.execute("ALTER TABLE cases ADD COLUMN linked_page_text TEXT")
        if "source_link_labels_json" not in columns:
            conn.execute("ALTER TABLE cases ADD COLUMN source_link_labels_json TEXT NOT NULL DEFAULT '[]'")


def save_cases(db_path: str | Path, cases: list[ParsedCase], slide_text_by_number: dict[int, str]) -> None:
    """Upsert parsed cases and their source slide records."""
    with connect(db_path) as conn:
        for case in cases:
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, title, pillar, client, services, brief, creative, impact,
                    source_deck, source_links_json, source_link_labels_json, linked_page_text, review_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(case_id) DO UPDATE SET
                    title=excluded.title,
                    pillar=excluded.pillar,
                    brief=excluded.brief,
                    creative=excluded.creative,
                    impact=excluded.impact,
                    source_deck=excluded.source_deck,
                    source_links_json=excluded.source_links_json,
                    source_link_labels_json=excluded.source_link_labels_json,
                    linked_page_text=COALESCE(excluded.linked_page_text, cases.linked_page_text),
                    client=COALESCE(excluded.client, cases.client),
                    services=COALESCE(excluded.services, cases.services),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    case.case_id, case.title, case.pillar, case.client, case.services,
                    case.brief, case.creative, case.impact, case.source_deck,
                    json.dumps(case.source_links), json.dumps(case.source_link_labels),
                    case.linked_page_text, case.review_status,
                ),
            )
            for slide_number in case.slide_numbers:
                conn.execute(
                    """
                    INSERT INTO slides (case_id, slide_number, extracted_text, hyperlinks_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(case_id, slide_number) DO UPDATE SET
                        extracted_text=excluded.extracted_text,
                        hyperlinks_json=excluded.hyperlinks_json
                    """,
                    (
                        case.case_id,
                        slide_number,
                        slide_text_by_number.get(slide_number, ""),
                        json.dumps(case.source_links),
                    ),
                )


def list_cases(db_path: str | Path) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   GROUP_CONCAT(s.slide_number, ',') AS slide_numbers
            FROM cases c LEFT JOIN slides s ON s.case_id = c.case_id
            GROUP BY c.case_id
            ORDER BY c.title COLLATE NOCASE
            """
        ).fetchall()
        return [dict(row) for row in rows]


def update_metadata(
    db_path: str | Path,
    case_id: str,
    field_name: str,
    new_value: str,
    updated_by: str,
    source: str = "user_entered",
    verification_status: str = "needs_review",
) -> None:
    allowed_fields = {"client", "services", "pillar"}
    if field_name not in allowed_fields:
        raise ValueError(f"Unsupported metadata field: {field_name}")

    with connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {field_name} FROM cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown case_id: {case_id}")

        old_value = row[field_name]
        conn.execute(
            f"UPDATE cases SET {field_name} = ?, updated_at=CURRENT_TIMESTAMP WHERE case_id=?",
            (new_value.strip() or None, case_id),
        )
        conn.execute(
            """
            INSERT INTO metadata_history (
                case_id, field_name, old_value, new_value, source,
                verification_status, updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id, field_name, old_value, new_value.strip() or None,
                source, verification_status, updated_by,
            ),
        )
