import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
from pptx import Presentation
from pptx.util import Inches
import pytest

from app.config import AISettings
from app.db.repository import connect, get_case, update_case_metadata
from app.search import embedding_service as vectors
from app.search import hybrid_search as search
from app.search import intent as interpretation
from app.search.classification import check_requirement, classify, quantified_quote
from app.search.documents import EMBEDDING_DIMENSIONS, MAX_INPUT_BYTES, bounded_text
from app.search.intent import Requirement, SearchIntent
from app.services.ingest_service import ingest_deck


def requirement(kind, terms=(), description=None, minimum=None):
    return Requirement(kind=kind,terms=list(terms),description=description or kind,minimum=minimum)


def intent(*mandatory, excluded=(), preferred=(), unresolved=()):
    return SearchIntent(summary="Find the requested production work",keywords=["hotel","AI","film"],
                        mandatory=list(mandatory),preferred=list(preferred),excluded=list(excluded),
                        unresolved_mandatory=list(unresolved))


@pytest.fixture
def corpus(tmp_path):
    deck = Presentation()
    samples = [
        ("HOTEL FILM","Vista Hotels commissioned a hospitality film.","Every scene was generated using AI.","The campaign reached 2 million viewers."),
        ("RAINFOREST","An unnamed organisation wanted an immersive experience.","Motion and touch-sensitive foliage made the installation interactive.","Six projectors and three screens were installed."),
        ("AI PRODUCT","Rival asked us to advertise its AI TV.","We filmed actors at a studio.","The campaign reached 3 million viewers."),
        ("LANGUAGES","A company needed multilingual event welcomes.","We used AI voice dubbing and lip-sync with human oversight.","With no need for additional filming, the content can be reused."),
        ("UNNAMED","An unnamed organisation needed a short film.","We filmed a short interview.","No measured result was supplied."),
        ("LIVE ACTION","A company commissioned a campaign film.","No generative AI was used. We filmed actors on location.","The campaign reached 500000 viewers."),
    ]
    for title,brief,creative,impact in samples:
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        for top,text in ((0,"CASE STUDY SYNTHETIC"),(1,title),(2,f"BRIEF\n{brief}\nCREATIVE\n{creative}\nIMPACT\n{impact}")):
            slide.shapes.add_textbox(Inches(1),Inches(top),Inches(8),Inches(2)).text = text
    source = tmp_path / "search.pptx"
    deck.save(source)
    db = tmp_path / "cases.sqlite3"
    imported = ingest_deck(source,db)
    ids = {case.title:case.case_id for case in imported["cases"]}
    return db,source,ids


@pytest.fixture
def models(monkeypatch):
    monkeypatch.setattr("app.config.get_ai_settings",lambda:AISettings("fake-key"))
    embed = MagicMock()
    embed.__enter__.return_value = embed

    def create(**kwargs):
        assert kwargs["dimensions"] == EMBEDDING_DIMENSIONS
        data = []
        for index,text in enumerate(kwargs["input"]):
            value = np.zeros(EMBEDDING_DIMENSIONS,dtype=np.float32)
            for term in search.keyword_terms(text):
                slot = int(hashlib.sha256(term.encode()).hexdigest()[:8],16)%EMBEDDING_DIMENSIONS
                value[slot] += 1
            value[0] += 0.1
            data.append(SimpleNamespace(index=index,embedding=value.tolist()))
        return SimpleNamespace(data=data,usage=SimpleNamespace(total_tokens=100))

    embed.embeddings.create.side_effect = create
    monkeypatch.setattr(vectors,"OpenAI",lambda **kwargs:embed)
    parse = MagicMock()
    parse.__enter__.return_value = parse
    parse.responses.parse.return_value = SimpleNamespace(status="completed",output_parsed=intent(),
                                                         usage=SimpleNamespace(input_tokens=100,output_tokens=50))
    monkeypatch.setattr(interpretation,"OpenAI",lambda **kwargs:parse)
    return embed,parse


def evidence_case(db,case_id):
    row = get_case(db,case_id)
    with connect(db) as conn:
        row["original_text"] = conn.execute("SELECT original_text FROM search_documents WHERE case_id=?",(case_id,)).fetchone()[0]
        metadata = {item["field_name"]:dict(item) for item in conn.execute("SELECT * FROM field_metadata WHERE case_id=?",(case_id,))}
    return row,metadata


def test_incremental_vectors_and_shared_cache_keep_distinct_case_identity(corpus,models,deck_variant):
    db,source,ids = corpus
    embed,_ = models
    first = vectors.build_index(db)
    assert first["embedded_texts"] == 6 and first["request_count"] == 1
    assert vectors.build_index(db)["request_count"] == 0
    repeated = ingest_deck(source,db)
    assert repeated["reused"] and vectors.index_status(db)["ready"] == 6
    second = ingest_deck(deck_variant(source),db)
    assert vectors.build_index(db)["request_count"] == 0
    assert vectors.index_status(db)["ready"] == 12
    matrix_ids,_ = vectors._matrix(str(db),"text-embedding-3-small",999)
    assert len(matrix_ids) == 12 and len(set(matrix_ids)) == 12
    update_case_metadata(db,ids["HOTEL FILM"],{"services":"A newly verified service"})
    assert vectors.index_status(db)["pending"] == 1
    assert vectors.build_index(db)["embedded_texts"] == 1
    assert embed.embeddings.create.call_count == 2
    assert second["source_id"] not in ids["HOTEL FILM"]


def test_query_interpretation_and_vector_cache_are_reused_without_case_reembedding(corpus,models):
    db,_,_ = corpus
    embed,parse = models
    vectors.build_index(db)
    first = search.search_cases(db,"AI hotel film",100)
    second = search.search_cases(db,"  AI   HOTEL film  ",100)
    assert first.mode == second.mode == "hybrid"
    assert second.intent_cache_hit and second.query_cache_hit
    assert parse.responses.parse.call_count == 1
    assert embed.embeddings.create.call_count == 2  # one index batch, one query
    assert parse.responses.parse.call_args.kwargs["store"] is False
    assert [row["case_id"] for row in first] == [row["case_id"] for row in second]


def test_confirmed_ai_metadata_alone_cannot_prove_a_mandatory_client(corpus):
    from app.db.repository import apply_ai_inferences
    db,_,ids = corpus
    case_id = ids["UNNAMED"]
    apply_ai_inferences(db,[{"case_id":case_id,"field_name":name,"value":value,"evidence":"An unnamed organisation needed a short film.",
                            "model":"fake","prompt_version":"v1","cache_key":"fake"}
                           for name,value in (("client","Imaginary Client"),("services","Film"))])
    row,metadata = evidence_case(db,case_id)
    assert row["review_status"] == "CONFIRMED"
    result = classify(row,metadata,intent(requirement("client",["Imaginary Client"])),1,1,["film"])
    assert result["match_level"] == "Partial Match"
    assert result["condition_checks"][0]["status"] == "UNKNOWN"


def test_original_commissioning_role_can_corroborate_client_inference(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    assert check_requirement(row,metadata,requirement("client",["Vista Hotels"]))["status"] == "MET"


def test_wrong_client_and_exclusion_override_perfect_semantic_similarity(corpus):
    db,_,ids = corpus
    update_case_metadata(db,ids["HOTEL FILM"],{"client":"Vista Hotels"})
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    assert classify(row,metadata,intent(requirement("client",["Rival"])),1,1,["film"])["match_level"] == "Not a Match"
    assert classify(row,metadata,intent(excluded=[requirement("client",["Vista Hotels"])]),1,1,["film"])["match_level"] == "Not a Match"
    assert classify(row,metadata,intent(excluded=[requirement("client",["Rival"])]),1,1,["film"])["match_level"] == "High Match"


def test_interactive_equipment_counts_do_not_prove_outcomes(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["RAINFOREST"])
    result = classify(row,metadata,intent(requirement("interactive"),requirement("quantified_outcomes")),1,1,["interactive"])
    assert result["match_level"] == "Partial Match"
    assert [check["status"] for check in result["condition_checks"]] == ["MET","UNKNOWN"]


@pytest.mark.parametrize("text,minimum,expected",[
    ("Earned media reach of 665 million.",1e6,True),
    ("The campaign had 93% positive sentiment from press.",90,True),
    ("The campaign reached 2 million viewers.",3e6,False),
    ("Our target is 5 million views.",None,False),
    ("Six projectors, 3 screens and 7 soundscapes.",None,False),
    ("TBC: campaign launches August 11, 2026.",None,False),
])
def test_quantified_outcome_evidence(text,minimum,expected):
    assert bool(quantified_quote(text,[],minimum)) == expected


def test_ai_product_and_explicit_no_ai_are_not_ai_production(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["AI PRODUCT"])
    assert check_requirement(row,metadata,requirement("ai_production"))["status"] == "UNKNOWN"
    row,metadata = evidence_case(db,ids["LIVE ACTION"])
    assert check_requirement(row,metadata,requirement("ai_production"))["status"] == "NOT_MET"


def test_dubbing_requirement_does_not_accept_any_generic_ai_film(corpus):
    db,_,ids = corpus
    req = requirement("ai_production",["dubbing","lip sync"])
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    assert check_requirement(row,metadata,req)["status"] == "UNKNOWN"
    row,metadata = evidence_case(db,ids["LANGUAGES"])
    assert check_requirement(row,metadata,req)["status"] == "MET"
    assert check_requirement(row,metadata,requirement("no_additional_filming"))["status"] == "MET"
    assert check_requirement(row,metadata,requirement("ai_production",["Adobe Firefly"]))["status"] == "UNKNOWN"


@pytest.mark.parametrize("terms",[
    ["generative AI","AI-generated","AI-made"],
    ["AI-made content"],
    ["AI-created content"],
    ["AI generation"],
])
def test_generic_ai_synonyms_do_not_hide_original_production_evidence(corpus,terms):
    db,_,ids=corpus
    req=requirement("ai_production",terms)
    row,metadata=evidence_case(db,ids["HOTEL FILM"])
    # Related wording must not override direct evidence, even with weak vectors.
    result=classify(row,metadata,intent(req),0.2,0.1,terms)
    assert result["match_level"]=="High Match"
    assert result["condition_checks"][0]["quote"]=="Every scene was generated using AI."
    row,metadata=evidence_case(db,ids["AI PRODUCT"])
    assert check_requirement(row,metadata,req)["status"]=="UNKNOWN"
    row,metadata=evidence_case(db,ids["LIVE ACTION"])
    assert check_requirement(row,metadata,req)["status"]=="NOT_MET"


def test_direct_evidence_does_not_bypass_missing_extra_mandatory_or_exclusion(corpus):
    db,_,ids=corpus
    row,metadata=evidence_case(db,ids["HOTEL FILM"])
    ai=requirement("ai_production",["AI-made"])
    parsed=intent(ai,requirement("ai_production",["dubbing"]))
    assert classify(row,metadata,parsed,0.2,0.1,["AI"])["match_level"]=="Partial Match"
    parsed=intent(ai,excluded=[requirement("content",["hospitality"])])
    assert classify(row,metadata,parsed,0.2,0.1,["AI"])["match_level"]=="Not a Match"


def test_case_cases_punctuation_and_ai_hyphen_share_interpretation_and_vector(corpus,models):
    db,_,ids=corpus
    embed,parse=models
    vectors.build_index(db)
    parsed=intent(requirement("ai_production"))
    parsed.summary="Cases featuring AI-generated content."
    parsed.keywords=["generative AI","AI-generated content"]
    parsed.mandatory[0].request_quote="ai generated content"
    parse.responses.parse.return_value.output_parsed=parsed
    variants=["Show me the cases about AI generated content!",
              "Show me the case about AI generated content!",
              "Show me cases about AI-Generated content",
              "Find case studies about AI generated content.",
              "AI generated content"]
    results=[search.search_cases(db,query,100) for query in variants]
    assert all(interpretation.normalize_query(query)=="ai generated content" for query in variants)
    assert all([(case["case_id"],case["match_level"]) for case in result]==
               [(case["case_id"],case["match_level"]) for case in results[0]] for result in results)
    assert next(case for case in results[0] if case["case_id"]==ids["HOTEL FILM"])["match_level"]=="High Match"
    assert all(result.intent["summary"]=="Cases featuring AI-generated content." for result in results)
    assert all(result.intent_cache_hit and result.query_cache_hit for result in results[1:])
    assert parse.responses.parse.call_count==1 and embed.embeddings.create.call_count==2


def test_query_normalization_preserves_must_haves_exclusions_and_numbers():
    query="Show me the cases about AI content. Must use lip-sync, exclude NHS and exceed 2 million views!"
    normalized=interpretation.normalize_query(query)
    assert normalized=="ai content. must use lip-sync, exclude nhs and exceed 2 million views"
    assert interpretation.normalize_query("Show me one case about AI content")!="ai content"
    assert interpretation.normalize_query(query.replace("2 million","3 million"))!=normalized


def test_old_non_english_intent_cache_is_not_reused_after_parser_policy_change(corpus,models):
    from app.config import get_ai_settings
    db,_,_=corpus
    _,parse=models
    query="Show me the cases about AI generated content!"
    key=hashlib.sha256(("search-intent-v4\n"+get_ai_settings().model+"\n"+query.casefold()).encode()).hexdigest()
    stale=intent()
    stale.summary="Casos sobre contenido generado con IA."
    with connect(db) as conn:
        conn.execute("INSERT INTO query_intent_cache(cache_key,payload_json) VALUES (?,?)",(key,stale.model_dump_json()))
    fresh,hit=interpretation.interpret_query(db,query)
    assert not hit and fresh.summary=="Find the requested production work"
    assert parse.responses.parse.call_count==1
    instructions=parse.responses.parse.call_args.kwargs["instructions"]
    assert "ALWAYS write summary" in instructions and "in ENGLISH" in instructions


def test_unknown_hard_condition_blocks_high_and_missing_metadata_remains_searchable(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    assert row["review_status"] == "NEEDS_REVIEW"
    assert classify(row,metadata,intent(requirement("ai_production")),1,1,["film"])["match_level"] == "High Match"
    assert classify(row,metadata,intent(unresolved=["Explicit redistribution permission"]),1,1,["film"])["match_level"] == "Partial Match"


def test_fts_updates_locally_and_service_unavailability_is_explicit(corpus):
    db,_,ids = corpus
    update_case_metadata(db,ids["HOTEL FILM"],{"client":"UniqueClientBefore"})
    assert search.search_cases(db,"UniqueClientBefore")[0]["case_id"] == ids["HOTEL FILM"]
    update_case_metadata(db,ids["HOTEL FILM"],{"client":"UniqueClientAfter"})
    assert not search.search_cases(db,"UniqueClientBefore")
    results = search.search_cases(db,"UniqueClientAfter")
    assert results.mode == "keyword" and results.notices
    assert results[0]["match_level"] != "High Match"


def test_missing_optional_preference_does_not_become_a_hard_constraint(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    parsed = intent(requirement("ai_production"),preferred=[requirement("content",["award winning"])])
    result = classify(row,metadata,parsed,1,1,["film"])
    assert result["match_level"] == "High Match"
    assert result["preferred_checks"][0]["status"] == "UNKNOWN"


def test_missing_specific_role_or_format_stays_unconfirmed_despite_related_content(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["LANGUAGES"])
    parsed = intent(requirement("ai_production",["dubbing"]),requirement("content",["leadership","executive"]))
    result = classify(row,metadata,parsed,1,1,["multilingual"])
    assert result["match_level"] == "Partial Match"
    assert result["condition_checks"][1]["status"] == "UNKNOWN"


def test_empty_library_does_not_call_openai(tmp_path,models):
    from app.db.repository import init_db
    db = tmp_path / "empty.sqlite3"
    init_db(db)
    results = search.search_cases(db,"Find an AI film")
    assert not results and results.notices
    embed,parse = models
    embed.embeddings.create.assert_not_called()
    parse.responses.parse.assert_not_called()


def test_link_type_checks_distinguish_versions(corpus):
    db,_,ids = corpus
    row,metadata = evidence_case(db,ids["HOTEL FILM"])
    req = requirement("link_type",["WATCH_VIDEO"])
    assert check_requirement(row,metadata,req)["status"] == "NOT_MET"
    row["source_link_labels_json"] = json.dumps([{"link_type":"WATCH_VIDEO"}])
    assert check_requirement(row,metadata,req)["status"] == "MET"


def test_embedding_input_is_bounded_and_malformed_query_is_not_fts_code(corpus):
    db,_,_ = corpus
    assert len(bounded_text("\u4e2d"*4000).encode()) <= MAX_INPUT_BYTES
    search.search_cases(db,'" ) OR * DROP TABLE cases; --')
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 6
    with pytest.raises(ValueError,match="shorten"):
        search.search_cases(db,"x"*7000)


def test_intent_conditions_are_grounded_in_request_and_related_examples_are_not_excluded(corpus,models):
    db,_,_ = corpus
    _,parse = models
    query = "Must use AI. Generic interviews are only related examples."
    parsed = intent(requirement("ai_production"))
    parsed.mandatory[0].request_quote = "Must use AI."
    unrelated = requirement("client",["Imaginary"])
    unrelated.request_quote = "The user never wrote this sentence."
    parsed.mandatory.append(unrelated)
    excluded = requirement("content",["interview"])
    excluded.request_quote = "Generic interviews are only related examples."
    parsed.excluded.append(excluded)
    parse.responses.parse.return_value.output_parsed = parsed
    result,_ = interpretation.interpret_query(db,query)
    assert [item.kind for item in result.mandatory] == ["ai_production"]
    assert not result.excluded
    assert result.unresolved_mandatory == ["client"]


def test_in_flight_old_embeddings_are_not_attached_to_changed_documents(corpus,models):
    db,_,ids = corpus
    embed,_ = models
    original = embed.embeddings.create.side_effect
    calls = 0

    def mutate_during_request(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            update_case_metadata(db,ids["HOTEL FILM"],{"client":"Updated client"})
        else:
            assert vectors.index_status(db)["pending"] == 1
        return original(**kwargs)

    embed.embeddings.create.side_effect = mutate_during_request
    result = vectors.build_index(db)
    assert calls == 2 and result["embedded_texts"] == 7
    assert vectors.index_status(db)["ready"] == 6


def test_model_change_invalidates_vectors_without_reusing_incompatible_cache(corpus,models,monkeypatch):
    db,_,_ = corpus
    vectors.build_index(db)
    monkeypatch.setattr("app.config.get_ai_settings",lambda:AISettings("fake-key",embedding_model="new-compatible-model"))
    assert vectors.index_status(db)["pending"] == 6
    assert vectors.build_index(db)["embedded_texts"] == 6
    assert vectors.index_status(db)["ready"] == 6


def test_failed_automatic_index_is_not_requeued_on_every_ui_rerun(corpus,models,monkeypatch):
    from app.services.task_service import TaskManager
    db,_,_ = corpus
    monkeypatch.setattr("app.services.task_service.build_index",lambda *args,**kwargs:
                        {"status":"FAILED","model":"text-embedding-3-small","error_code":"UNAVAILABLE"})
    manager = TaskManager(db)
    try:
        first = manager.ensure_indexing()
        assert first and manager.wait(first)["status"] == "SUCCEEDED"
        assert manager.ensure_indexing() is None
        retry = manager.ensure_indexing(force=True)
        assert retry and manager.wait(retry)["status"] == "SUCCEEDED"
    finally:
        manager.shutdown()
