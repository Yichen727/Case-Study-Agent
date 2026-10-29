"""One-time conservative refresh from existing source snapshots.

Run without --apply to review counts. Human-edited fields are never replaced.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.db.repository import connect, init_db, list_sources
from app.ingestion.pptx_parser import parse_pptx
from app.db.migrations import refresh_case_statuses
from app.ingestion.validation import validate_pptx


def repair_fields(db_path: Path, apply: bool = False) -> dict:
    init_db(db_path)
    changes = []
    skipped = 0
    for source in list_sources(db_path):
        snapshot = source["source_snapshot_reference"]
        if not snapshot or not Path(snapshot).is_file():
            skipped += 1
            continue
        with Path(snapshot).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != source["content_sha256"]:
            raise ValueError("A snapshot no longer matches its imported hash; refresh stopped.")
        validate_pptx(snapshot)
        parsed = {slide.original_slide_id: slide for slide in parse_pptx(snapshot) if slide.is_case_study}
        with connect(db_path) as conn:
            cases = conn.execute("SELECT * FROM cases WHERE source_id=?", (source["source_id"],)).fetchall()
            protected = {(row["case_id"], row["field_name"]) for row in conn.execute(
                "SELECT case_id, field_name FROM field_metadata WHERE updated_by IS NOT NULL"
            )}
        for case in cases:
            slide = parsed.get(case["original_slide_id"])
            if slide is None:
                continue
            values = {"title": slide.case_name, "client": slide.client, "services": slide.services,
                      "brief": slide.brief, "creative": slide.creative, "impact": slide.impact}
            for field, value in values.items():
                missing = case[field] is None or (field == "title" and case[field].startswith("Untitled case on slide "))
                if missing and value and (case["case_id"], field) not in protected:
                    changes.append((case["case_id"], field, case[field], value, source["source_id"], slide.original_slide_id))
            for field, value in (("source_links_json", json.dumps(slide.hyperlinks)),
                                 ("source_link_labels_json", json.dumps(slide.hyperlink_labels))):
                if case[field] != value:
                    changes.append((case["case_id"], field, case[field], value, source["source_id"], slide.original_slide_id))
    report = {"applied": apply, "planned_field_changes": len(changes), "changed_cases": len({c[0] for c in changes}),
              "skipped_sources_without_snapshots": skipped, "changes_by_field": {}, "backup": None, "written_fields": 0}
    for _, field, *_ in changes:
        report["changes_by_field"][field] = report["changes_by_field"].get(field, 0) + 1
    if not apply or not changes:
        return report
    folder = db_path.parent / "backups"
    folder.mkdir(exist_ok=True)
    backup = folder / f"{db_path.stem}.before-stage2-{uuid4().hex[:8]}.sqlite3"
    with connect(db_path) as reader, closing(sqlite3.connect(backup)) as destination:
        reader.backup(destination)
    report["backup"] = str(backup)
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for case_id, field, old, new, source_id, slide_id in changes:
            metadata_field = "source_links" if field.startswith("source_link") else field
            edited = conn.execute("SELECT updated_by FROM field_metadata WHERE case_id=? AND field_name=?",
                                  (case_id, metadata_field)).fetchone()
            if edited and edited["updated_by"] is not None:
                continue
            cursor = conn.execute(
                f"""UPDATE cases SET {field}=?, updated_at=CURRENT_TIMESTAMP
                    WHERE case_id=? AND {field} IS ?""", (new, case_id, old))
            if not cursor.rowcount:
                continue
            report["written_fields"] += 1
            if field != "source_link_labels_json":
                conn.execute(
                    """INSERT INTO field_metadata(case_id,field_name,value,source_kind,evidence_reference,verification_status)
                       VALUES (?,?,?,'SLIDE_EXTRACTED',?,'EXTRACTED')
                       ON CONFLICT(case_id,field_name) DO UPDATE SET value=excluded.value,source_kind=excluded.source_kind,
                       evidence_reference=excluded.evidence_reference,verification_status='EXTRACTED',updated_at=CURRENT_TIMESTAMP""",
                    (case_id, metadata_field, new, f"source:{source_id}/slide:{slide_id}"))
            conn.execute(
                """INSERT INTO metadata_history(case_id,field_name,old_value,new_value,source,verification_status,updated_by)
                   VALUES (?,?,?,?,'original_case_material','unverified','parser_stage2')""",
                (case_id, field, old, new))
        if report["written_fields"]:
            refresh_case_statuses(conn,[change[0] for change in changes])
            conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/case_studies.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = repair_fields(args.database, args.apply)
    report["recorded_at"] = datetime.now(timezone.utc).isoformat()
    if args.apply and report["written_fields"]:
        Path("evaluation/stage2_refresh.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
