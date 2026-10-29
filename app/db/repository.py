from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from app.db.migrations import initialize_database, METADATA_FIELDS, refresh_case_statuses
from app.models import FieldProvenance, ParsedCase, SourceRecord, missing_value
from app.search.documents import sync_documents


CASE_SELECT = """
SELECT c.*, src.source_type, src.source_snapshot_reference, src.requires_reimport,
       src.imported_at, GROUP_CONCAT(s.slide_number, ',') AS slide_numbers,
       (SELECT GROUP_CONCAT(m.field_name) FROM field_metadata m WHERE m.case_id=c.case_id
        AND m.source_kind='AI_INFERRED' AND m.verification_status!='VERIFIED') AS unverified_ai_fields
FROM cases c
JOIN sources src ON src.source_id = c.source_id
LEFT JOIN slides s ON s.case_id = c.case_id
"""


@contextmanager
def connect(db_path: str | Path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: str | Path) -> bool:
    return initialize_database(db_path)


def save_cases(
    db_path: str | Path,
    cases: list[ParsedCase],
    slide_text_by_number: dict[int, str],
    *,
    source: SourceRecord,
) -> None:
    """Commit one source and its independent cases atomically.

    Re-saving the same immutable import does not overwrite human metadata.
    Different presentations have independent source IDs, including identical filenames.
    The ingestion service reuses an existing local import when file bytes match.
    """
    for case in cases:
        if (
            case.source_id != source.source_id
            or case.original_slide_id is None
            or case.case_id != f"{source.source_id}:{case.original_slide_id}"
            or len(case.slide_numbers) != 1
            or case.source_deck != source.filename
        ):
            raise ValueError("Case identity must belong to exactly one original source slide.")
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted_source = conn.execute(
            """INSERT INTO sources (
                source_id, source_type, filename, original_source_url, source_file_id,
                source_version, imported_at, ingestion_status,
                source_snapshot_reference, content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO NOTHING""",
            (
                source.source_id, source.source_type, source.filename, source.original_source_url,
                source.source_file_id, source.source_version, source.imported_at,
                source.ingestion_status, source.source_snapshot_reference, source.content_sha256,
            ),
        )
        registered = conn.execute(
            "SELECT filename, source_type, content_sha256, source_snapshot_reference FROM sources WHERE source_id=?",
            (source.source_id,),
        ).fetchone()
        if tuple(registered) != (
            source.filename, source.source_type, source.content_sha256, source.source_snapshot_reference
        ):
            raise ValueError("An existing source snapshot cannot be replaced.")
        changed = bool(inserted_source.rowcount)
        for case in cases:
            inserted = conn.execute(
                """INSERT INTO cases (
                    case_id, source_id, original_slide_id, title, pillar, client, services,
                    brief, creative, impact, source_deck, source_links_json,
                    source_link_labels_json, linked_page_text, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEEDS_REVIEW')
                ON CONFLICT(case_id) DO NOTHING""",
                (
                    case.case_id, case.source_id, case.original_slide_id, case.title,
                    case.pillar, case.client, case.services, case.brief, case.creative,
                    case.impact, case.source_deck, json.dumps(case.source_links),
                    json.dumps(case.source_link_labels), case.linked_page_text,
                ),
            )
            if not inserted.rowcount:
                continue
            changed = True
            slide_number = case.slide_numbers[0]
            conn.execute(
                """INSERT INTO slides(case_id, slide_number, extracted_text, hyperlinks_json)
                   VALUES (?, ?, ?, ?)""",
                (case.case_id, slide_number, slide_text_by_number[slide_number],
                 json.dumps(case.source_links)),
            )
            values = {name: getattr(case, name) for name in METADATA_FIELDS}
            values.update(
                source_links=json.dumps(case.source_links),
                original_slide_id=str(case.original_slide_id),
                slide_number=str(slide_number),
                linked_page_text=case.linked_page_text,
            )
            for name, value in values.items():
                provenance = case.field_provenance.get(
                    name, FieldProvenance("UNKNOWN", verification_status="UNVERIFIED")
                )
                conn.execute(
                    """INSERT INTO field_metadata (
                        case_id, field_name, value, source_kind, evidence_reference, verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (case.case_id, name, value, provenance.source_kind,
                     provenance.evidence_reference, provenance.verification_status),
                )
        if changed:
            refresh_case_statuses(conn,[case.case_id for case in cases])
            sync_documents(conn,[case.case_id for case in cases])
            conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")


def list_cases(db_path: str | Path, limit: int | None = None, offset: int = 0, source_id: str | None = None) -> list[dict]:
    sql = CASE_SELECT
    params = []
    if source_id is not None:
        sql += " WHERE c.source_id=?"
        params.append(source_id)
    sql += " GROUP BY c.case_id ORDER BY c.title COLLATE NOCASE, c.source_id, s.slide_number"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend((limit, offset))
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def count_cases(db_path: str | Path) -> int:
    with connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def data_version(db_path: str | Path) -> int:
    with connect(db_path) as conn:
        return conn.execute("SELECT data_version FROM app_state WHERE singleton=1").fetchone()[0]


def get_case(db_path: str | Path, case_id: str) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(CASE_SELECT + " WHERE c.case_id=? GROUP BY c.case_id", (case_id,)).fetchone()
        return dict(row) if row else None


def list_case_options(db_path: str | Path, limit: int = 25, offset: int = 0) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            """SELECT c.case_id,c.title,c.source_deck,c.source_id,c.review_status,GROUP_CONCAT(s.slide_number,',') AS slide_numbers
               FROM cases c LEFT JOIN slides s ON s.case_id=c.case_id GROUP BY c.case_id
               ORDER BY c.title COLLATE NOCASE,c.source_id LIMIT ? OFFSET ?""", (limit,offset)).fetchall()]


def list_metadata_options(db_path, *, show_all=False, retained_case_id=None):
    """All lightweight choices; only the selected case loads full text."""
    sql = """SELECT c.case_id,c.title,c.source_deck,c.source_id,c.review_status,
                    GROUP_CONCAT(s.slide_number,',') AS slide_numbers
             FROM cases c LEFT JOIN slides s ON s.case_id=c.case_id"""
    params = []
    if not show_all:
        sql += " WHERE c.review_status='NEEDS_REVIEW' OR c.case_id=?"
        params.append(retained_case_id)
    sql += " GROUP BY c.case_id ORDER BY c.title COLLATE NOCASE,c.source_id,s.slide_number"
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(sql,params)]


def case_options_by_ids(db_path: str | Path, case_ids: list[str]) -> list[dict]:
    if not case_ids:
        return []
    with connect(db_path) as conn:
        placeholders = ",".join("?" for _ in case_ids)
        return [dict(row) for row in conn.execute(
            f"""SELECT c.case_id,c.title,c.source_deck,c.source_id,c.review_status,GROUP_CONCAT(s.slide_number,',') AS slide_numbers,
                       EXISTS(SELECT 1 FROM field_metadata m WHERE m.case_id=c.case_id
                              AND m.source_kind='AI_INFERRED' AND m.verification_status!='VERIFIED') AS unverified_ai
                FROM cases c LEFT JOIN slides s ON s.case_id=c.case_id
                WHERE c.case_id IN ({placeholders}) GROUP BY c.case_id""", case_ids).fetchall()]


def list_sources(db_path: str | Path) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM sources ORDER BY imported_at, source_id"
        ).fetchall()]


def get_field_metadata(db_path: str | Path, case_id: str) -> list[dict]:
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            """SELECT field_name, value, source_kind, evidence_reference,
                      verification_status, evidence_text, model, prompt_version, updated_by, updated_at
               FROM field_metadata WHERE case_id=? ORDER BY field_name""",
            (case_id,),
        ).fetchall()]


def update_metadata(
    db_path: str | Path,
    case_id: str,
    field_name: str,
    new_value: str,
    updated_by: str,
    source: str = "user_entered",
    verification_status: str = "needs_review",
) -> None:
    update_case_metadata(db_path,case_id,{field_name:new_value},updated_by,
                         source=source,verification_status=verification_status)


def update_case_metadata(
    db_path, case_id, values, updated_by="local_user", *,
    source="user_entered", verification_status="confirmed",
):
    """Save one metadata form atomically and recalculate completeness once."""
    if not values:
        return
    if not set(values).issubset(METADATA_FIELDS):
        raise ValueError("Unsupported metadata field.")
    source_kinds = {
        "user_entered":"HUMAN_ENTERED", "original_case_material":"SLIDE_EXTRACTED",
        "authorised_external_source":"LINK_EXTRACTED",
    }
    verification = {"needs_review":"UNVERIFIED", "unverified":"UNVERIFIED", "confirmed":"VERIFIED"}
    if source not in source_kinds or verification_status not in verification:
        raise ValueError("Unsupported metadata provenance or verification status.")
    cleaned = {name:None if missing_value(value) else value.strip() for name,value in values.items()}
    if "title" in cleaned and cleaned["title"] is None:
        raise ValueError("Case name cannot be empty or Unknown.")
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise ValueError("This case is no longer available.")
        for field_name,cleaned_value in cleaned.items():
            old_metadata = conn.execute("SELECT * FROM field_metadata WHERE case_id=? AND field_name=?",
                                        (case_id,field_name)).fetchone()
            previous = dict(old_metadata) if old_metadata else None
            keep_inference = (previous and previous["source_kind"] == "AI_INFERRED"
                              and cleaned_value == row[field_name])
            provenance = {
                "source_kind":previous["source_kind"] if keep_inference else source_kinds[source],
                "evidence_reference":previous["evidence_reference"] if keep_inference else f"review:{updated_by}",
                "verification_status":verification[verification_status] if cleaned_value is not None else "UNVERIFIED",
                "evidence_text":previous["evidence_text"] if keep_inference else None,
                "model":previous["model"] if keep_inference else None,
                "prompt_version":previous["prompt_version"] if keep_inference else None,
            }
            conn.execute(f"UPDATE cases SET {field_name}=?,updated_at=CURRENT_TIMESTAMP WHERE case_id=?",
                         (cleaned_value,case_id))
            conn.execute(
                """INSERT INTO field_metadata(case_id,field_name,value,source_kind,evidence_reference,
                   verification_status,evidence_text,model,prompt_version,updated_by) VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id,field_name) DO UPDATE SET value=excluded.value,source_kind=excluded.source_kind,
                   evidence_reference=excluded.evidence_reference,verification_status=excluded.verification_status,
                   evidence_text=excluded.evidence_text,model=excluded.model,prompt_version=excluded.prompt_version,
                   updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                (case_id,field_name,cleaned_value,*provenance.values(),updated_by))
            record_metadata_history(conn,case_id,field_name,row[field_name],cleaned_value,
                                    previous,provenance,updated_by,source)
        refresh_case_statuses(conn,[case_id])
        sync_documents(conn,[case_id])
        conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")


def apply_enrichment(db_path: str | Path, updates: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """Fill missing machine fields, preserving edits made while HTTP was running."""
    written = []
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for case_id, name, value, url in updates:
            if name not in {"client", "services", "linked_page_text"}:
                raise ValueError("Unsupported enrichment field")
            cursor = conn.execute(
                f"""UPDATE cases SET {name}=?,updated_at=CURRENT_TIMESTAMP
                    WHERE case_id=? AND {name} IS NULL AND NOT EXISTS (
                    SELECT 1 FROM field_metadata m WHERE m.case_id=cases.case_id AND m.field_name=? AND m.updated_by IS NOT NULL)""",
                (value, case_id, name))
            if not cursor.rowcount:
                continue
            written.append((case_id, name, value, url))
            conn.execute(
                """INSERT INTO field_metadata(case_id,field_name,value,source_kind,evidence_reference,verification_status)
                   VALUES (?,?,?,'LINK_EXTRACTED',?,'EXTRACTED')
                   ON CONFLICT(case_id,field_name) DO UPDATE SET value=excluded.value,source_kind='LINK_EXTRACTED',
                   evidence_reference=excluded.evidence_reference,verification_status='EXTRACTED',updated_at=CURRENT_TIMESTAMP""",
                (case_id,name,value,url))
        if written:
            refresh_case_statuses(conn,[row[0] for row in written])
            sync_documents(conn,[row[0] for row in written])
            conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
    return written


def record_metadata_history(conn, case_id, field_name, old_value, new_value, previous, current, editor, source):
    conn.execute(
        """INSERT INTO metadata_history(case_id,field_name,old_value,new_value,source,verification_status,
           updated_by,previous_provenance_json,new_provenance_json) VALUES (?,?,?,?,?,?,?,?,?)""",
        (case_id,field_name,old_value,new_value,source,current["verification_status"],editor,
         json.dumps(previous) if previous else None,json.dumps(current)))


def get_case_text(db_path, case_id):
    with connect(db_path) as conn:
        return "\n".join(row[0] for row in conn.execute(
            "SELECT extracted_text FROM slides WHERE case_id=? ORDER BY slide_number", (case_id,)))


def get_metadata_history(db_path, case_id):
    with connect(db_path) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM metadata_history WHERE case_id=? ORDER BY history_id DESC LIMIT 50", (case_id,))]


def apply_ai_inferences(db_path, updates):
    """Fill gaps without overwriting human edits or field verification."""
    written = 0
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for update in updates:
            case_id, name = update["case_id"], update["field_name"]
            if name not in {"client", "services"} or missing_value(update["value"]):
                raise ValueError("AI may only infer supported Client or Services values.")
            previous_row = conn.execute("SELECT * FROM field_metadata WHERE case_id=? AND field_name=?",
                                        (case_id,name)).fetchone()
            previous = dict(previous_row) if previous_row else None
            cursor = conn.execute(
                f"""UPDATE cases SET {name}=?,updated_at=CURRENT_TIMESTAMP
                    WHERE case_id=?
                    AND ({name} IS NULL OR lower(trim({name})) IN ('','none','unknown'))
                    AND NOT EXISTS (SELECT 1 FROM field_metadata m WHERE m.case_id=cases.case_id
                                    AND m.field_name=? AND (m.updated_by IS NOT NULL OR m.verification_status='VERIFIED'))""",
                (update["value"],case_id,name))
            if not cursor.rowcount:
                continue
            provenance = {"source_kind": "AI_INFERRED", "evidence_reference": f"ai:{update['cache_key']}",
                          "verification_status": "UNVERIFIED", "evidence_text": update["evidence"],
                          "model": update["model"], "prompt_version": update["prompt_version"]}
            conn.execute(
                """INSERT INTO field_metadata(case_id,field_name,value,source_kind,evidence_reference,
                   verification_status,evidence_text,model,prompt_version) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(case_id,field_name) DO UPDATE SET value=excluded.value,source_kind=excluded.source_kind,
                   evidence_reference=excluded.evidence_reference,verification_status='UNVERIFIED',
                   evidence_text=excluded.evidence_text,model=excluded.model,prompt_version=excluded.prompt_version,
                   updated_at=CURRENT_TIMESTAMP""", (case_id,name,update["value"],*provenance.values()))
            record_metadata_history(conn,case_id,name,previous["value"] if previous else None,update["value"],
                                    previous,provenance,"openai", "AI_INFERRED")
            written += 1
        if written:
            refresh_case_statuses(conn,[update["case_id"] for update in updates])
            sync_documents(conn,[update["case_id"] for update in updates])
            conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
    return written
