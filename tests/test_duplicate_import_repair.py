from pathlib import Path

from app.db.repository import connect, init_db, list_cases, save_cases, update_metadata
from app.ingestion.pptx_parser import build_cases, parse_pptx
from app.services.source_service import release_snapshot, snapshot_local_source
from evaluation.repair_duplicate_imports import consolidate

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation/fixtures"


def old_import(db, filename="source_a.pptx"):
    init_db(db)
    source = snapshot_local_source(FIXTURES / filename,db,filename)
    slides = parse_pptx(source.source_snapshot_reference)
    cases = build_cases(slides,source.filename,source.source_id)
    source.ingestion_status = "PARSED"
    save_cases(db,cases,{slide.slide_number:slide.text for slide in slides},source=source)
    release_snapshot(source,db,committed=True)
    return source,cases


def test_repair_backs_up_preserves_history_and_only_consolidates_identical_decks(tmp_path):
    db = tmp_path / "cases.sqlite3"
    first,cases = old_import(db)
    second,duplicates = old_import(db)
    third,other = old_import(db,"source_b.pptx")
    for case in (cases[0],duplicates[0]):
        update_metadata(db,case.case_id,"client","Retained client","reviewer")
    preview = consolidate(db)
    assert preview["removable_cases"] == 2 and len(list_cases(db)) == 5
    result = consolidate(db,apply=True)
    assert result["removed_cases"] == 2 and result["moved_history"] == 1
    assert Path(result["backup"]).is_file()
    assert len(list_cases(db)) == 3
    assert {case["source_id"] for case in list_cases(db)} == {first.source_id,third.source_id}
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metadata_history").fetchone()[0] == 2
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("SELECT COUNT(*) FROM cases_fts").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0] == 3
    with connect(result["backup"]) as saved:
        assert saved.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 5
    assert consolidate(db,apply=True)["removed_cases"] == 0


def test_repair_preserves_a_duplicate_with_different_manual_metadata(tmp_path):
    db = tmp_path / "cases.sqlite3"
    old_import(db)
    duplicate,cases = old_import(db)
    update_metadata(db,cases[0].case_id,"client","Different client correction","reviewer")
    result = consolidate(db,apply=True)
    assert result["removed_cases"] == 0 and result["backup"] is None
    assert result["skipped_sources"] == [duplicate.source_id]
    assert len(list_cases(db)) == 4
