"""Offline REST integration tests: real repository, retrieval and classification."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
import pytest

from app.api import main as api
from app.api.openapi import build_copilot_spec
from app.db.repository import (
    apply_ai_inferences, connect, init_db, save_cases, update_case_metadata,
)
from app.models import FieldProvenance, ParsedCase, REQUIRED_CASE_FIELDS, SourceRecord
from app.search import hybrid_search
from app.search.embedding_service import SearchServiceError
from app.search.intent import Requirement, SearchIntent

TEST_KEY = "test-only-api-credential-0000000000000000"
HEADERS = {"X-API-Key": TEST_KEY}
AI_CONDITION = {"kind": "ai_production", "description": "AI used to produce the work"}


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("CASE_STUDY_API_KEY", TEST_KEY)
    db = tmp_path / "cases.sqlite3"
    init_db(db)
    ids = []
    snapshots = []
    for source_id, filename, numbers in (("first", "first.pptx", (26, 27, 28)),
                                         ("second", "second.pptx", (26,))):
        snapshot = tmp_path / filename
        snapshot.write_bytes(b"Test-only source bytes: " + source_id.encode())
        snapshots.append(snapshot)
        source = SourceRecord(source_id, filename, str(snapshot),
                              hashlib.sha256(snapshot.read_bytes()).hexdigest(), "2026-10-30T12:00:00Z")
        cases, texts = [], {}
        for number in numbers:
            case_id = f"{source_id}:{number + 230}"
            ai = number != 27
            name = "GEN AI BRAND FILM" if number == 26 else "LIVE ACTION" if number == 27 else "UNNAMED CLIENT"
            client = "Vista Hotels" if number != 28 else None
            brief = "Vista Hotels commissioned a hotel film." if client else "An unnamed organisation needed a film."
            creative = "Every scene was generated using AI." if ai else "No generative AI was used. Actors were filmed in a studio."
            impact = "The campaign reached 2 million viewers."
            case = ParsedCase(case_id, name, "CONTENT", client, "Film", brief, creative, impact,
                              filename, [number], source_id=source_id, original_slide_id=number + 230,
                              field_provenance={field: FieldProvenance("SLIDE_EXTRACTED") for field in REQUIRED_CASE_FIELDS})
            cases.append(case)
            texts[number] = "\n".join((name, brief, creative, impact))
            ids.append(case_id)
        save_cases(db, cases, texts, source=source)
    # Completeness can be Confirmed while an inferred client is still unverified.
    apply_ai_inferences(db, [{"case_id": "first:258", "field_name": "client", "value": "Imaginary Client",
                             "evidence": "An unnamed organisation needed a film.", "model": "fake",
                             "prompt_version": "test", "cache_key": "test"}])
    return db, ids, snapshots


@pytest.fixture
def backend(library, monkeypatch):
    db, ids, _ = library
    parsed = SearchIntent(summary="AI-generated films", keywords=["AI", "film"],
                          mandatory=[Requirement(**AI_CONDITION, terms=[], minimum=None)],
                          preferred=[], excluded=[], unresolved_mandatory=[])
    # Only model/network boundaries are replaced; the search engine is real.
    monkeypatch.setattr(hybrid_search, "interpret_query", lambda *_: (parsed, True))
    monkeypatch.setattr(hybrid_search, "index_status", lambda *_: {"total": len(ids), "ready": len(ids), "pending": 0})
    monkeypatch.setattr(hybrid_search, "query_vector", lambda *_: ([1.0], True))
    monkeypatch.setattr(hybrid_search, "semantic_scores", lambda *_: dict.fromkeys(ids, 0.9))
    return db, parsed


@pytest.fixture
def client(backend):
    with TestClient(api.create_app(db_path=backend[0])) as instance:
        yield instance


def post(client, **payload):
    return client.post("/api/search", json={"query": "Find AI-generated films", **payload}, headers=HEADERS)


def test_results_reuse_engine_and_keep_duplicate_names_and_page_numbers(client, backend, monkeypatch):
    observed = Mock(wraps=api.search_cases)
    monkeypatch.setattr(api, "search_cases", observed)
    response = post(client)
    assert response.status_code == 200
    data = response.json()
    assert data["search_mode"] == "hybrid"
    rows = data["results"]
    observed.assert_called_once()
    assert observed.call_args.args == (backend[0], "Find AI-generated films", 5)
    names = [row for row in rows if row["case_name"] == "GEN AI BRAND FILM"]
    assert len(names) == 2 and len({row["case_id"] for row in names}) == 2
    assert {row["source_file"] for row in names} == {"first.pptx", "second.pptx"}
    assert {row["slide_number"] for row in names} == {26}
    for row in names:
        assert row["match_level"] == "High Match"
        assert row["review_status"] == "CONFIRMED" and row["client"] == "Vista Hotels"
        assert row["supporting_evidence"][0]["quote"] == "Every scene was generated using AI."
        assert row["condition_checks"][0]["status"] == "MET"
    assert rows[-1]["match_level"] == "Not a Match"
    assert "source_snapshot_reference" not in json.dumps(data)
    assert "_rank" not in json.dumps(data)
    assert len(post(client, limit=2).json()["results"]) == 2


def test_explicit_conditions_are_additive_and_do_not_mutate_cached_intent(client, backend):
    condition = {"kind": "ai_production", "description": "Must use AI dubbing", "terms": ["dubbing"]}
    rows = post(client, mandatory_conditions=[condition]).json()["results"]
    original = next(row for row in rows if row["case_id"] == "first:256")
    assert original["match_level"] == "Partial Match"
    assert [check["status"] for check in original["condition_checks"]] == ["MET", "UNKNOWN"]
    assert "Must use AI dubbing" in original["match_reason"]
    assert len(backend[1].mandatory) == 1
    assert post(client).json()["results"][0]["match_level"] == "High Match"


def test_explicit_preferences_missing_evidence_does_not_become_mandatory(client):
    rows = post(client, preferred_conditions=[{"kind": "content", "description": "Prefer award winners",
                                              "terms": ["award winning"]}]).json()["results"]
    assert rows[0]["match_level"] == "High Match"
    assert rows[0]["preferred_checks"][0]["status"] == "UNKNOWN"


def test_ai_inferred_client_alone_cannot_prove_mandatory_even_if_confirmed(client):
    rows = post(client, mandatory_conditions=[{"kind": "client", "description": "Client is Imaginary Client",
                                              "terms": ["Imaginary Client"]}]).json()["results"]
    row = next(row for row in rows if row["case_id"] == "first:258")
    assert row["review_status"] == "CONFIRMED"
    assert row["match_level"] == "Partial Match"
    assert row["condition_checks"][-1]["status"] == "UNKNOWN"
    assert any("AI-inferred metadata" in message for message in row["uncertainties"])
    assert next(item for item in rows if item["case_id"] == "first:256")["match_level"] == "Not a Match"


def test_quantified_minimum_missing_metadata_and_exclusions_keep_existing_rules(client, backend):
    update_case_metadata(backend[0], "first:256", {"impact": None})
    condition = {"kind": "quantified_outcomes", "description": "At least 3 million viewers",
                 "terms": ["viewers"], "minimum": 3000000}
    row = next(row for row in post(client, mandatory_conditions=[condition]).json()["results"]
               if row["case_id"] == "first:256")
    assert row["review_status"] == "NEEDS_REVIEW"
    assert row["match_level"] == "Partial Match" and row["condition_checks"][-1]["status"] == "UNKNOWN"
    assert any("Missing metadata: impact" in item for item in row["uncertainties"])
    backend[1].excluded.append(Requirement(kind="client", description="Exclude Vista Hotels",
                                         terms=["Vista Hotels"], minimum=None))
    rows = post(client, mandatory_conditions=[AI_CONDITION]).json()["results"]
    row = next(row for row in rows if row["case_id"] == "second:256")
    assert row["match_level"] == "Not a Match"
    assert "Exclude Vista Hotels" in row["match_reason"]


@pytest.mark.parametrize("changes", [
    {"query": ""}, {"query": "   "}, {"query": "!!!"}, {"query": "a" * 6001},
    {"query": "\u4e2d" * 2001}, {"query": 123}, {"limit": 0}, {"limit": 11}, {"limit": "5"}, {"limit": True},
    {"unexpected": "field"}, {"mandatory_conditions": [{"kind": "invented", "description": "Unsupported"}]},
    {"mandatory_conditions": [{"kind": "client", "description": "Client without terms"}]},
    {"mandatory_conditions": [{"kind": "client", "description": "Client", "terms": [" " ]}]},
    {"mandatory_conditions": [{"kind": "client", "description": "Client", "terms": ["x" * 101]}]},
    {"mandatory_conditions": [{"kind": "ai_production", "description": "AI", "minimum": 2}]},
    {"mandatory_conditions": [{"kind": "link_type", "description": "Link", "terms": ["OTHER"]}]},
    {"mandatory_conditions": [AI_CONDITION] * 9}, {"preferred_conditions": [AI_CONDITION] * 7},
])
def test_request_validation_rejects_bad_inputs_without_running_search(client, monkeypatch, changes):
    search = Mock()
    monkeypatch.setattr(api, "search_cases", search)
    response = post(client, **changes)
    assert response.status_code == 422
    assert response.json()["error"] == "INVALID_REQUEST"
    assert "input" not in response.json()
    search.assert_not_called()


@pytest.mark.parametrize("headers, suffix", [({}, ""), ({"X-API-Key": "wrong"}, ""),
                                             ({}, "?api_key=" + TEST_KEY)])
def test_authentication_is_required_and_query_parameters_are_not_credentials(client, monkeypatch, headers, suffix):
    search = Mock()
    monkeypatch.setattr(api, "search_cases", search)
    response = client.post("/api/search" + suffix, json={"query": "AI film"}, headers=headers)
    assert response.status_code == 401 and response.json()["error"] == "UNAUTHORIZED"
    assert TEST_KEY not in response.text
    search.assert_not_called()


@pytest.mark.parametrize("key", [None, "", "too-short"])
def test_no_key_means_no_api_startup(tmp_path, monkeypatch, key):
    if key is None:
        monkeypatch.delenv("CASE_STUDY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("CASE_STUDY_API_KEY", key)
    with pytest.raises(RuntimeError, match="CASE_STUDY_API_KEY"):
        api.create_app(db_path=tmp_path / "must-not-exist.sqlite3")
    assert not (tmp_path / "must-not-exist.sqlite3").exists()


def test_no_unauthenticated_docs_routes(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_empty_library_and_no_matches_are_successful_empty_results(library, client, monkeypatch):
    empty = library[0].parent / "empty.sqlite3"
    init_db(empty)
    # Exercise the real early return for an empty database.
    from app.search.embedding_service import index_status
    monkeypatch.setattr(hybrid_search, "index_status", index_status)
    with TestClient(api.create_app(db_path=empty)) as empty_client:
        response = post(empty_client)
    assert response.status_code == 200 and response.json()["results"] == []
    assert response.json()["notices"]
    monkeypatch.setattr(hybrid_search, "semantic_scores", lambda *_: {})
    monkeypatch.setattr(hybrid_search, "interpret_query", lambda *_: (
        SearchIntent(summary="No matches", keywords=["nonexistentxyz"], mandatory=[], preferred=[],
                     excluded=[], unresolved_mandatory=[]), False))
    assert post(client, query="nonexistentxyz").json()["results"] == []


def test_unavailable_interpretation_does_not_silently_ignore_structured_conditions(library):
    # No model mocks or credentials: existing offline degradation must stay honest.
    with TestClient(api.create_app(db_path=library[0])) as instance:
        response = post(instance, mandatory_conditions=[AI_CONDITION])
    assert response.status_code == 200
    rows = response.json()["results"]
    assert rows and response.json()["search_mode"] == "keyword"
    assert response.json()["notices"]
    assert all(row["match_level"] != "High Match" for row in rows)
    row = next(row for row in rows if row["case_id"] == "first:256")
    assert [check["status"] for check in row["condition_checks"]] == ["MET"]
    assert "Free-text requirements could not be interpreted." in row["uncertainties"]


@pytest.mark.parametrize("kind", ["missing", "old", "corrupt"])
def test_missing_old_or_corrupt_database_is_not_created_or_migrated(library, kind):
    path = library[0].parent / "unprepared" / "cases.sqlite3"
    if kind != "missing":
        path.parent.mkdir()
        path.write_bytes(b"" if kind == "old" else b"not a database")
    before = path.read_bytes() if path.exists() else None
    with TestClient(api.create_app(db_path=path)) as instance:
        response = post(instance)
    assert response.status_code == 503 and response.json()["error"] == "LIBRARY_UNAVAILABLE"
    assert str(path) not in response.text
    if kind == "missing":
        assert not path.parent.exists()
    else:
        assert path.read_bytes() == before


@pytest.mark.parametrize("failure, status, code", [
    (SearchServiceError("private-upstream-detail"), 503, "SEARCH_UNAVAILABLE"),
    (RuntimeError("private-upstream-detail"), 500, "SEARCH_FAILED"),
])
def test_search_errors_do_not_expose_internal_information(client, monkeypatch, caplog, failure, status, code):
    monkeypatch.setattr(api, "search_cases", Mock(side_effect=failure))
    response = post(client)
    assert response.status_code == status and response.json()["error"] == code
    assert "private-upstream-detail" not in response.text + caplog.text
    assert TEST_KEY not in response.text + caplog.text


def test_search_does_not_change_case_records_metadata_or_source_files(client, library):
    tables = ("sources", "cases", "slides", "field_metadata", "metadata_history", "search_documents", "app_state")

    def records():
        with connect(library[0]) as conn:
            return {table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                    for table in tables}

    before = records()
    snapshots = [path.read_bytes() for path in library[2]]
    assert post(client, mandatory_conditions=[AI_CONDITION]).status_code == 200
    assert records() == before
    assert [path.read_bytes() for path in library[2]] == snapshots


def test_copilot_spec_is_swagger2_and_agrees_with_runtime_contract(client):
    spec = build_copilot_spec()
    assert spec == json.loads(Path("docs/copilot-openapi.json").read_text(encoding="utf-8"))
    assert spec["swagger"] == "2.0"
    assert spec["security"] == [{"ApiKey": []}]
    assert spec["securityDefinitions"]["ApiKey"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    assert set(spec["paths"]) == {"/api/search"}
    operation = spec["paths"]["/api/search"]["post"]
    live = client.app.openapi()["paths"]["/api/search"]["post"]
    assert operation["operationId"] == live["operationId"]
    assert set(operation["responses"]) == set(live["responses"])
    limit = spec["definitions"]["SearchRequest"]["properties"]["limit"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (5, 1, 10)
    serialized = json.dumps(spec)
    assert "anyOf" not in serialized and "$defs" not in serialized
    assert TEST_KEY not in serialized and "openapi" not in spec

    def check_refs(node):
        if isinstance(node, dict):
            if "$ref" in node:
                assert node["$ref"].startswith("#/definitions/")
                assert node["$ref"].split("/")[-1] in spec["definitions"]
            for value in node.values():
                check_refs(value)
        elif isinstance(node, list):
            for value in node:
                check_refs(value)

    check_refs(spec)
    assert build_copilot_spec("https://cases.example.com")["schemes"] == ["https"]


@pytest.mark.parametrize("url", ["http://cases.example.com", "https://user:secret@cases.example.com",
                                 "https://cases.example.com?key=secret", "https://cases.example.com/api"])
def test_copilot_spec_rejects_insecure_or_credential_bearing_remote_origins(url):
    with pytest.raises(ValueError):
        build_copilot_spec(url)
