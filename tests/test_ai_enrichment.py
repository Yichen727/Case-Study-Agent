import json
from pathlib import Path
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from openai import AuthenticationError
from pptx import Presentation
from pptx.util import Inches
import pytest

from app.config import AISettings
from app.db.repository import connect, get_field_metadata, list_cases, update_metadata
from app.models import REQUIRED_CASE_FIELDS, case_display_status
from app.services import ai_enrichment as ai
from app.services import ingest_service

CLIENT_QUOTE = "Acme commissioned a film about its staff."
SERVICES_QUOTE = "We scripted and filmed interviews."


def make_deck(folder, count=2):
    deck = Presentation()
    for index in range(count):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for top,text in ((0,"CASE STUDY SYNTHETIC"),(1,f"STAFF FILM {index}"),
                         (2,f"BRIEF\n{CLIENT_QUOTE}\nCREATIVE\n{SERVICES_QUOTE}\nIMPACT\nOutcome not provided.")):
            shape = slide.shapes.add_textbox(Inches(1),Inches(top), Inches(8),Inches(2))
            shape.text = text
    path = folder / "ai-fixture.pptx"
    deck.save(path)
    return path


def supported_response(**kwargs):
    items = json.loads(kwargs["input"])
    return SimpleNamespace(status="completed",usage=SimpleNamespace(input_tokens=100,output_tokens=50),
                           output_parsed=ai.InferenceBatch(cases=[ai.CaseInference(
                               item_id=item["item_id"],client=ai.InferredField(value="Acme",evidence=CLIENT_QUOTE),
                               services=ai.InferredField(value="Scriptwriting and filming",evidence=SERVICES_QUOTE)) for item in items]))


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_service,"fetch_case_page",lambda url, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    path = make_deck(tmp_path)
    imported = ingest_service.ingest_deck(path,db)
    return db,path,imported


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(ai,"get_ai_settings",lambda: AISettings("test-key","gpt-5.4-mini"))
    client = Mock()
    client.responses.parse.side_effect = supported_response
    constructor = Mock(return_value=client)
    monkeypatch.setattr(ai,"OpenAI",constructor)
    return client,constructor


def test_without_key_or_missing_fields_no_api_call(dataset, monkeypatch):
    db,_,result = dataset
    constructor = Mock()
    monkeypatch.setattr(ai,"OpenAI",constructor)
    assert ai.infer_missing_metadata(result["source_id"],db)["status"] == "NOT_CONFIGURED"
    constructor.assert_not_called()


def test_infers_only_two_fields_keeps_outcomes_and_reuses_cache_across_imports(dataset, api,deck_variant):
    db,path,imported = dataset
    before = list_cases(db)
    original_bytes = path.read_bytes()
    client,constructor = api
    stats = ai.infer_missing_metadata(imported["source_id"],db)
    assert stats["status"] == "COMPLETED" and stats["request_count"] == 1
    assert stats["applied_fields"] == 4 and stats["input_tokens"] == 100 and stats["output_tokens"] == 50
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["store"] is False and kwargs["max_output_tokens"] == ai.MAX_OUTPUT_TOKENS
    assert constructor.call_args.kwargs["max_retries"] == 0
    assert "test-key" not in kwargs["input"] and str(db) not in kwargs["input"]
    for old,new in zip(before,list_cases(db)):
        assert all(new[name] == old[name] for name in ("brief","creative","impact"))
        assert new["review_status"] == "CONFIRMED" and case_display_status(new) == "Confirmed"
        assert set(new["unverified_ai_fields"].split(",")) == {"client","services"}
        provenance = {row["field_name"]:row for row in get_field_metadata(db,new["case_id"])}
        assert provenance["client"]["source_kind"] == "AI_INFERRED"
        assert provenance["client"]["verification_status"] == "UNVERIFIED"
        assert provenance["client"]["evidence_text"] == CLIENT_QUOTE
        assert provenance["client"]["model"] == "gpt-5.4-mini"
    assert path.read_bytes() == original_bytes
    repeated = ingest_service.ingest_deck(path,db)
    assert repeated["reused"] and repeated["ai"]["status"] == "SKIPPED"
    assert len(list_cases(db)) == 2
    second = ingest_service.ingest_deck(deck_variant(path),db)
    assert second["ai"]["cache_hits"] == 2 and second["ai"]["request_count"] == 0
    assert client.responses.parse.call_count == 1
    assert second["source_id"] != imported["source_id"] and len(list_cases(db)) == 4
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0] == 2


def test_unanchored_claims_stay_unknown_and_unknown_results_are_cached(dataset, api):
    db,_,imported = dataset
    client,_ = api

    def unsupported(**kwargs):
        response = supported_response(**kwargs)
        for item in response.output_parsed.cases:
            item.client.evidence = "This quotation does not appear on the slide."
            item.services.value = None
        return response

    client.responses.parse.side_effect = unsupported
    stats = ai.infer_missing_metadata(imported["source_id"],db)
    assert stats["applied_fields"] == 0 and stats["unknown_fields"] == 4
    assert all(row["client"] is None and row["services"] is None for row in list_cases(db))
    assert ai.infer_missing_metadata(imported["source_id"],db)["cache_hits"] == 2
    assert client.responses.parse.call_count == 1


@pytest.mark.parametrize("mode",["refusal","incomplete","wrong_ids"])
def test_invalid_or_refused_output_is_not_written(dataset, api, mode):
    db,_,imported = dataset
    client,_ = api

    def invalid(**kwargs):
        response = supported_response(**kwargs)
        if mode == "refusal":
            response.output_parsed = None
        elif mode == "incomplete":
            response.status = "incomplete"
        else:
            response.output_parsed.cases[0].item_id = "not-requested"
        return response

    client.responses.parse.side_effect = invalid
    stats = ai.infer_missing_metadata(imported["source_id"],db)
    assert stats["status"] == "FAILED" and stats["error_code"] == "AI_INVALID_OUTPUT"
    assert client.responses.parse.call_count == 1
    assert all(row["client"] is None for row in list_cases(db))


def test_api_auth_failure_is_safe_and_does_not_rollback_valid_import(dataset, api,deck_variant):
    db,path,_ = dataset
    client,_ = api
    client.responses.parse.side_effect = AuthenticationError(
        "Bearer SECRET private diagnostic",response=httpx.Response(401,request=httpx.Request("POST","https://api.openai.com/v1/responses")),body=None)
    result = ingest_service.ingest_deck(deck_variant(path),db)
    assert result["ai"]["status"] == "FAILED" and result["ai"]["error_code"] == "AI_AUTH_FAILED"
    assert "SECRET" not in json.dumps(result["ai"])
    assert len(list_cases(db)) == 4
    with connect(db) as conn:
        row = dict(conn.execute("SELECT * FROM ai_runs ORDER BY rowid DESC LIMIT 1").fetchone())
        assert row["error_code"] == "AI_AUTH_FAILED" and "SECRET" not in json.dumps(row)


def test_human_corrections_and_clears_cannot_be_replaced_in_flight(dataset, api):
    db,_,imported = dataset
    client,_ = api
    entered,release = Event(),Event()

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return supported_response(**kwargs)

    client.responses.parse.side_effect = held
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(ai.infer_missing_metadata,imported["source_id"],db)
        try:
            assert entered.wait(5)
            first,second = list_cases(db)
            update_metadata(db,first["case_id"],"client","","reviewer")
            update_metadata(db,first["case_id"],"services","Human service","reviewer")
            update_metadata(db,second["case_id"],"client","Human client","reviewer")
            update_metadata(db,second["case_id"],"services","Human service","reviewer")
        finally:
            release.set()
        assert future.result()["applied_fields"] == 0
    rows = list_cases(db)
    assert rows[0]["client"] is None and rows[0]["services"] == "Human service"
    assert rows[1]["client"] == "Human client" and rows[1]["review_status"] == "CONFIRMED"
    client.responses.parse.reset_mock()
    assert ai.infer_missing_metadata(imported["source_id"],db)["status"] == "NO_WORK"
    client.responses.parse.assert_not_called()


def test_case_limit_batch_size_and_budget_are_bounded(tmp_path, monkeypatch, api):
    monkeypatch.setattr(ingest_service,"fetch_case_page",lambda url, **kwargs: {})
    # Seed without AI to control which explicit task performs the calls.
    monkeypatch.setattr("app.services.ingest_service.infer_missing_metadata",lambda *args, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    imported = ingest_service.ingest_deck(make_deck(tmp_path,7),db)
    monkeypatch.setattr(ai,"MAX_CASES_PER_TASK",4)
    monkeypatch.setattr(ai,"BATCH_SIZE",2)
    client,_ = api
    stats = ai.infer_missing_metadata(imported["source_id"],db)
    assert stats["status"] == "PARTIAL" and stats["skipped_cases"] == 3
    assert stats["request_count"] == 2 and stats["applied_fields"] == 8
    assert all(len(json.loads(call.kwargs["input"])) <= 2 for call in client.responses.parse.call_args_list)
    monkeypatch.setattr(ai,"TASK_BUDGET_SECONDS",0)
    assert ai.infer_missing_metadata(imported["source_id"],db)["error_code"] == "AI_BUDGET_REACHED"
    assert client.responses.parse.call_count == 2


def test_model_and_prompt_version_are_part_of_cache_identity(dataset, api, monkeypatch,deck_variant):
    db,path,imported = dataset
    client,_ = api
    ai.infer_missing_metadata(imported["source_id"],db)
    monkeypatch.setattr(ai,"PROMPT_VERSION","client-services-test-next")
    second = ingest_service.ingest_deck(deck_variant(path,"next-prompt.pptx"),db)
    assert second["ai"]["request_count"] == 1
    monkeypatch.setattr(ai,"get_ai_settings",lambda: AISettings("test-key","another-compatible-model"))
    third = ingest_service.ingest_deck(deck_variant(path,"next-model.pptx"),db)
    assert third["ai"]["request_count"] == 1 and client.responses.parse.call_count == 3


def test_local_env_configuration_never_reveals_key(tmp_path, monkeypatch):
    from app.config import get_ai_settings
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=SECRET_LOCAL\nOPENAI_METADATA_MODEL=gpt-5.4-mini\n",encoding="utf-8")
    monkeypatch.setattr("app.config.ENV_FILE",env)
    assert get_ai_settings().api_key == "SECRET_LOCAL"
    assert "SECRET_LOCAL" not in repr(get_ai_settings())
    monkeypatch.setenv("OPENAI_API_KEY","SECRET_ENV")
    assert get_ai_settings().api_key == "SECRET_ENV"


def test_ai_tasks_use_background_runner_and_keep_safe_usage_summary(dataset, api):
    from app.services.task_service import TaskManager
    db,_,imported = dataset
    manager = TaskManager(db)
    try:
        task_id = manager.submit_ai_enrichment(imported["source_id"],"fixture.pptx")
        task = manager.wait(task_id)
        assert task["status"] == "SUCCEEDED"
        result = json.loads(task["result_json"])
        assert result["ai"]["applied_fields"] == 4 and "test-key" not in task["result_json"]
    finally:
        manager.shutdown()


def test_cached_unknowns_do_not_starve_later_cases_past_task_limit(tmp_path, monkeypatch, api):
    monkeypatch.setattr(ingest_service,"infer_missing_metadata",lambda *args, **kwargs: {})
    db = tmp_path / "cases.sqlite3"
    source = ingest_service.ingest_deck(make_deck(tmp_path,3),db)["source_id"]
    monkeypatch.setattr(ai,"MAX_CASES_PER_TASK",2)
    client,_ = api

    def unknowns(**kwargs):
        response = supported_response(**kwargs)
        for item in response.output_parsed.cases:
            item.client = ai.InferredField(value=None,evidence=None)
            item.services = ai.InferredField(value=None,evidence=None)
        return response

    client.responses.parse.side_effect = unknowns
    first = ai.infer_missing_metadata(source,db)
    assert first["skipped_cases"] == 1
    second = ai.infer_missing_metadata(source,db)
    assert second["cache_hits"] == 2 and second["skipped_cases"] == 0
    assert second["request_count"] == 1 and client.responses.parse.call_count == 2


def test_wrapped_quotes_are_normalized_but_unsupported_evidence_is_still_rejected():
    proposed = ai.InferredField(value="Acme",evidence=f'"{CLIENT_QUOTE}"')
    accepted = ai._supported(proposed,CLIENT_QUOTE,"client")
    assert accepted.value == "Acme" and accepted.evidence == CLIENT_QUOTE
    assert ai._supported(proposed,"A different organisation commissioned this work.","client").value is None


@pytest.mark.parametrize("field",["brief","creative","impact"])
def test_ai_completion_keeps_cases_missing_sections_in_needs_review(dataset, api, field):
    db,_,imported = dataset
    target = imported["cases"][0].case_id
    update_metadata(db,target,field,"","local_user")
    ai.infer_missing_metadata(imported["source_id"],db)
    row = next(row for row in list_cases(db) if row["case_id"] == target)
    assert row["client"] == "Acme" and row["services"]
    assert row[field] is None and row["review_status"] == "NEEDS_REVIEW"
