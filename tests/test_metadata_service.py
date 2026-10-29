import json
from pathlib import Path

import pytest

from app.db.repository import (
    apply_ai_inferences, connect, data_version, get_case, get_case_text,
    get_field_metadata, get_metadata_history, list_metadata_options, update_case_metadata, update_metadata,
)
from app.models import REQUIRED_CASE_FIELDS, case_display_status
from app.search.keyword_search import search_cases
from app.services import ingest_service


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_service,"fetch_case_page",lambda url, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    path = Path(__file__).resolve().parents[1] / "evaluation/fixtures/source_a.pptx"
    result = ingest_service.ingest_deck(path,db)
    case_id = result["cases"][0].case_id
    apply_ai_inferences(db,[{"case_id":case_id,"field_name":"client","value":"Fixture client",
                            "evidence":"Synthetic test brief.","model":"test-model","prompt_version":"v1","cache_key":"test-cache"}])
    return db,case_id


def test_automatic_confirmation_retains_ai_origin_without_human_verification(case):
    db,case_id = case
    assert get_case(db,case_id)["review_status"] == "NEEDS_REVIEW"
    apply_ai_inferences(db,[{"case_id":case_id,"field_name":"services","value":"Filming",
                            "evidence":"Invented fixture content.","model":"test-model","prompt_version":"v1","cache_key":"services-cache"}])
    row = get_case(db,case_id)
    assert row["review_status"] == "CONFIRMED" and case_display_status(row) == "Confirmed"
    assert any(result["case_id"] == case_id for result in search_cases(db,"Fixture client"))
    field = next(row for row in get_field_metadata(db,case_id) if row["field_name"] == "client")
    assert field["source_kind"] == "AI_INFERRED" and field["verification_status"] == "UNVERIFIED"
    assert field["updated_by"] is None and field["model"] == "test-model"
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM case_reviews").fetchone()[0] == 0


def test_editing_ai_value_retains_old_inference_and_original_slide(case):
    db,case_id = case
    original = get_case_text(db,case_id)
    update_case_metadata(db,case_id,{"client":"Corrected client","services":"Editing"})
    history = next(row for row in get_metadata_history(db,case_id) if row["field_name"] == "client")
    previous = json.loads(history["previous_provenance_json"])
    assert previous["source_kind"] == "AI_INFERRED" and previous["model"] == "test-model"
    assert previous["evidence_text"] == "Synthetic test brief."
    assert json.loads(history["new_provenance_json"])["source_kind"] == "HUMAN_ENTERED"
    assert get_case(db,case_id)["review_status"] == "CONFIRMED"
    assert get_case_text(db,case_id) == original


@pytest.mark.parametrize("field",REQUIRED_CASE_FIELDS)
def test_each_missing_core_field_requires_review(field):
    row = {name:"Present" for name in REQUIRED_CASE_FIELDS}
    assert case_display_status(row) == "Confirmed"
    row[field] = None
    row["review_status"] = "APPROVED"  # Historic approval cannot hide missing content.
    assert case_display_status(row) == "Needs Review"


def test_batch_save_is_atomic_and_recalculates_status_once(case):
    db,case_id = case
    version = data_version(db)
    before = get_case(db,case_id)
    with pytest.raises(ValueError,match="Case name cannot be empty"):
        update_case_metadata(db,case_id,{"services":"Editing","title":""})
    assert get_case(db,case_id) == before and data_version(db) == version
    update_case_metadata(db,case_id,{"client":"Human client","services":"Editing"})
    assert data_version(db) == version + 1
    assert get_case(db,case_id)["review_status"] == "CONFIRMED"
    update_case_metadata(db,case_id,{"services":"Unknown"})
    assert get_case(db,case_id)["services"] is None
    assert get_case(db,case_id)["review_status"] == "NEEDS_REVIEW"


def test_review_choices_are_unpaginated_filter_incomplete_and_retain_current_case(case):
    db,case_id = case
    update_case_metadata(db,case_id,{"services":"Editing"})
    assert case_id not in {row["case_id"] for row in list_metadata_options(db)}
    assert case_id in {row["case_id"] for row in list_metadata_options(db,show_all=True)}
    assert case_id in {row["case_id"] for row in list_metadata_options(db,retained_case_id=case_id)}
