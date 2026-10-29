from pathlib import Path

from pptx import Presentation
from pptx.util import Inches

from app.db.repository import connect, list_cases, update_metadata
from app.services import ingest_service
from evaluation.repair_stage2_fields import repair_fields


def test_refresh_fills_missing_parser_fields_without_overwriting_human_changes(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
    deck = Presentation(fixture)
    deck.slides[0].shapes[1].text = "CASE STUDY: FIXED TITLE"
    deck.slides[0].shapes.add_textbox(Inches(0.5), Inches(5), Inches(5), Inches(1)).text = "Client: Original Client"
    source = tmp_path / "source.pptx"
    deck.save(source)
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr(ingest_service, "fetch_case_page", lambda url, **kwargs: {})
    result = ingest_service.ingest_deck(source, db)
    case_id = result["cases"][0].case_id
    update_metadata(db, case_id, "client", "", "human-reviewer")
    with connect(db) as conn:
        conn.execute("UPDATE cases SET title='Untitled case on slide 1', brief=NULL WHERE case_id=?", (case_id,))
    preview = repair_fields(db)
    assert preview["changes_by_field"] == {"title": 1, "brief": 1}
    assert preview["written_fields"] == 0
    applied = repair_fields(db, apply=True)
    assert applied["written_fields"] == 2
    assert Path(applied["backup"]).is_file()
    rows = {row["case_id"]: row for row in list_cases(db)}
    assert len(rows) == 2
    assert rows[case_id]["title"] == "FIXED TITLE" and rows[case_id]["brief"]
    assert rows[case_id]["client"] is None
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM metadata_history WHERE updated_by='human-reviewer'").fetchone()[0] == 1
    assert repair_fields(db, apply=True)["written_fields"] == 0
