"""Back up and consolidate identical local imports without discarding edit history."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.config import DB_PATH
from app.db.repository import connect


def source_cases(conn, source_id):
    result = {}
    for row in conn.execute("SELECT * FROM cases WHERE source_id=? ORDER BY original_slide_id",(source_id,)):
        case = dict(row)
        slide_id = case["original_slide_id"]
        if slide_id is None:
            return None
        case_id = case["case_id"]
        values = {key:value for key,value in case.items()
                  if key not in {"case_id","source_id","source_deck","created_at","updated_at"}}
        values["slides"] = [tuple(item) for item in conn.execute(
            "SELECT slide_number,extracted_text,hyperlinks_json FROM slides WHERE case_id=? ORDER BY slide_number",(case_id,))]
        values["metadata"] = []
        for item in conn.execute(
            """SELECT field_name,value,source_kind,evidence_reference,verification_status,evidence_text,
                      model,prompt_version,updated_by FROM field_metadata WHERE case_id=? ORDER BY field_name""",(case_id,)):
            metadata = list(item)
            reference = metadata[3]
            if reference and reference.startswith(f"source:{source_id}/"):
                metadata[3] = reference.replace(f"source:{source_id}/","source:<same-import>/",1)
            values["metadata"].append(tuple(metadata))
        result[slide_id] = (case_id,values)
    return result


def plan_consolidation(conn):
    sources = conn.execute("""SELECT * FROM sources WHERE source_type='LOCAL' AND content_sha256 IS NOT NULL
                              AND ingestion_status='PARSED' AND requires_reimport=0
                              AND EXISTS (SELECT 1 FROM cases c WHERE c.source_id=sources.source_id)
                              ORDER BY imported_at,source_id""").fetchall()
    originals, pairs, skipped = {}, [], []
    for source in sources:
        checksum = source["content_sha256"]
        current = source_cases(conn,source["source_id"])
        if checksum not in originals:
            originals[checksum] = (source["source_id"],current)
            continue
        canonical_id,canonical = originals[checksum]
        if not current or not canonical or {key:value[1] for key,value in current.items()} != {key:value[1] for key,value in canonical.items()}:
            skipped.append(source["source_id"])
            continue
        pairs.append({"keep_source_id":canonical_id,"duplicate_source_id":source["source_id"],
                      "cases":[(current[key][0],canonical[key][0]) for key in current]})
    return pairs,skipped


def consolidate(db_path, *, apply=False):
    database = Path(db_path).resolve()
    report = {"applied":False,"backup":None,"removed_cases":0,"moved_history":0,"moved_reviews":0}
    with connect(database) as conn:
        if apply:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM tasks WHERE status IN ('QUEUED','RUNNING') LIMIT 1").fetchone():
                raise RuntimeError("Wait for background tasks to finish before consolidating imports.")
        pairs,skipped = plan_consolidation(conn)
        report.update(duplicate_imports=len(pairs),skipped_sources=skipped,
                      removable_cases=sum(len(pair["cases"]) for pair in pairs))
        if not apply or not pairs:
            return report
        backup = database.parent / "backups" / f"{database.stem}.before-dedup-{uuid4().hex[:8]}.sqlite3"
        backup.parent.mkdir(exist_ok=True)
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro",uri=True)) as reader, closing(sqlite3.connect(backup)) as saved:
            reader.backup(saved)
        report["backup"] = str(backup)
        for pair in pairs:
            for duplicate,canonical in pair["cases"]:
                report["moved_history"] += conn.execute("UPDATE metadata_history SET case_id=? WHERE case_id=?",(canonical,duplicate)).rowcount
                report["moved_reviews"] += conn.execute("UPDATE case_reviews SET case_id=? WHERE case_id=?",(canonical,duplicate)).rowcount
                conn.execute("DELETE FROM cases_fts WHERE case_id=?",(duplicate,))
                report["removed_cases"] += conn.execute("DELETE FROM cases WHERE case_id=?",(duplicate,)).rowcount
        # Retain source records and AI usage runs as historical provenance.
        conn.execute("UPDATE app_state SET data_version=data_version+1 WHERE singleton=1")
        report["applied"] = True
        report["remaining_cases"] = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DB_PATH)
    parser.add_argument("--apply",action="store_true",help="Back up first, then consolidate only matching cases and provenance.")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    report = consolidate(args.database,apply=args.apply)
    output = json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.write_text(output+"\n",encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
