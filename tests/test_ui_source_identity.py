from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import ElementTree
from streamlit.proto.WidgetStates_pb2 import WidgetState

from app.db.repository import list_cases
from app.search.keyword_search import search_cases
from app.services.ingest_service import ingest_deck
from app.ingestion.pptx_parser import parse_pptx
from app.services.task_service import get_task_manager


@pytest.fixture(autouse=True)
def browser_tabs_and_shutdown(monkeypatch):
    # AppTest does not yet serialize stateful tabs. Supply the state sent by a browser.
    original = ElementTree.get_widget_states

    def widget_states(tree):
        states = original(tree)
        for block in tree.get("tab_container"):
            if not block.proto.tab_container.id:
                continue
            states.widgets.append(WidgetState(id=block.proto.tab_container.id,
                                              string_value=tree.session_state["active_tab"]))
        return states

    monkeypatch.setattr(ElementTree, "get_widget_states", widget_states)
    # AppTest cannot operate the custom frontend; browser evaluation covers dragging and the cross button.
    monkeypatch.setattr("app.ui.selection_panel.selected_case_list",lambda **kwargs:SimpleNamespace(action=None))
    yield
    from app.services.task_service import _managers
    for manager in _managers.values():
        manager.shutdown()


def tab(app, name):
    app.session_state["active_tab"] = name
    app.run()
    assert not app.exception


def finish_imports(app, db):
    manager = get_task_manager(db)
    for task in manager.list_tasks("local"):
        manager.wait(task["task_id"])
    app.run()
    assert not app.exception


@pytest.mark.parametrize("empty_file", [False, True])
def test_ui_initializes_empty_database_even_after_previous_initialization(tmp_path, monkeypatch, empty_file):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    if empty_file:
        db.touch()
    monkeypatch.setattr("app.config.DB_PATH", db)
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: [])
    app = AppTest.from_file(str(root / "app" / "ui" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    assert list_cases(db) == []
    app.session_state["search_results"] = [{"case_id": "stale-search-result"}]
    db.unlink()
    if empty_file:
        db.touch()
    app.run(timeout=30)
    assert not app.exception
    assert list_cases(db) == []
    assert "search_results" not in app.session_state


@pytest.mark.parametrize("same_filename", [False, True])
def test_ui_imports_two_decks_and_displays_distinct_sources(tmp_path, monkeypatch, same_filename):
    root = Path(__file__).resolve().parents[1]
    uploads = []
    for filename in ("source_a.pptx", "source_b.pptx"):
        upload = BytesIO((root / "evaluation" / "fixtures" / filename).read_bytes())
        upload.name = "same-name.pptx" if same_filename else filename
        uploads.append(upload)
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH", db)
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: uploads)
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page", lambda url, **kwargs: {})
    app = AppTest.from_file(str(root / "app" / "ui" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    next(button for button in app.button if button.label == "Parse and save decks").click().run()
    finish_imports(app, db)
    assert not app.exception
    rows = list_cases(db)
    assert len(rows) == 3
    assert len({row["case_id"] for row in rows}) == 3
    assert len({row["source_id"] for row in rows}) == 2
    tab(app, "All cases")
    displayed = app.dataframe[0].value
    assert len(displayed) == 3
    assert set(displayed["Source deck"]) == ({"same-name.pptx"} if same_filename else {"source_a.pptx", "source_b.pptx"})
    assert list(displayed.columns) == ["Title", "Source deck", "Slides", "Pillar", "Client", "Services", "Status"]
    assert all("Import:" not in item.value for item in app.success)
    next(field for field in app.text_area if field.label == "Search cases").set_value("SYNTHETIC: SAME NAME")
    next(button for button in app.button if button.label == "Search").click().run()
    assert not app.exception
    assert len(app.session_state["search_results"]) == 3
    source_a_id = next(row["source_id"] for row in rows if row["slide_numbers"] == "2")
    target = next(row for row in rows
                  if row["source_id"] == source_a_id and row["slide_numbers"] == "1")
    visible = "\n".join(item.value for item in app.caption)
    assert all(row["case_id"] not in visible and row["source_id"] not in visible for row in rows)
    first_pages = [row["case_id"] for row in rows if row["slide_numbers"] == "1"]
    for case_id in first_pages:
        app.checkbox(key=f"case-select:{case_id}").check().run()
    tab(app, "Selected cases")
    assert not app.multiselect
    assert not app.exception
    assert app.session_state["selected_case_ids"] == tuple(first_pages)
    next(button for button in app.button if button.label=="Confirm order").click().run()
    assert app.session_state["confirmed_case_ids"] == tuple(first_pages)
    assert not next(button for button in app.button if button.label=="Generate PPTX").disabled
    assert all(item.label!="Export" for item in app.tabs)
    tab(app,"Search results")
    app.checkbox(key=f"case-select:{first_pages[0]}").uncheck().run()
    assert not app.exception
    assert app.session_state["selected_case_ids"] == (first_pages[1],)
    assert app.session_state["confirmed_case_ids"] is None
    tab(app,"Search results")
    assert app.checkbox(key=f"case-select:{first_pages[0]}").value is False
    assert app.checkbox(key=f"case-select:{first_pages[1]}").value is True
    tab(app, "Needs Review")
    next(field for field in app.selectbox if field.label == "Choose a case").select(target["case_id"]).run()
    next(field for field in app.text_input if field.label == "Client").set_value("Only source A")
    next(button for button in app.button if button.label == "Save metadata").click().run()
    assert not app.exception
    assert app.session_state["active_tab"] == "Needs Review"
    assert app.get("tab_container")[0].proto.tab_container.default_tab_index == 2
    assert next(field for field in app.selectbox if field.label == "Choose a case").value == target["case_id"]
    rows = list_cases(db)
    assert next(row for row in rows if row["case_id"] == target["case_id"])["client"] == "Only source A"
    assert all(row["client"] is None for row in rows if row["case_id"] != target["case_id"])
    assert all(row["review_status"] == "NEEDS_REVIEW" for row in rows)
    tab(app, "All cases")
    displayed = app.dataframe[0].value
    assert displayed["Client"].eq("Only source A").sum() == 1


def test_ui_confirms_complete_metadata_and_search_uses_enter_enabled_form(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH", db)
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page", lambda url, **kwargs: {
        "text": "Synthetic page evidence", "client": "Synthetic client", "services": None,
    })
    result = ingest_deck(root / "evaluation" / "fixtures" / "source_a.pptx", db)
    target_id = result["cases"][0].case_id
    search = Mock(wraps=search_cases)
    monkeypatch.setattr("app.search.keyword_search.search_cases", search)
    app = AppTest.from_file(str(root / "app" / "ui" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    assert not app.dataframe  # Inactive tabs do not query/render their tables.
    tab(app, "All cases")
    assert set(app.dataframe[0].value["Status"]) == {"Needs Review"}
    form = next(block for block in app.get("form") if block.proto.form.form_id == "case_search")
    assert not form.proto.form.enter_to_submit
    next(field for field in app.text_area if field.label == "Search cases").set_value("SYNTHETIC: SAME NAME").run()
    search.assert_not_called()
    next(button for button in app.button if button.label == "Search").click().run()
    search.assert_called_once_with(str(db), "SYNTHETIC: SAME NAME", 100)
    assert len(app.session_state["search_results"]) == 2

    tab(app, "Needs Review")
    next(field for field in app.selectbox if field.label == "Choose a case").select(target_id).run()
    next(field for field in app.text_input if field.label == "Services").set_value("Editing")
    next(button for button in app.button if button.label == "Save metadata").click().run()
    assert not app.exception
    assert app.session_state["active_tab"] == "Needs Review"
    assert next(field for field in app.text_input if field.label == "Services").value == "Editing"
    tab(app, "All cases")
    displayed = app.dataframe[0].value.set_index("Slides")
    assert displayed.loc["1", "Status"] == "Confirmed"
    assert displayed.loc["2", "Status"] == "Needs Review"
    assert {row["review_status"] for row in list_cases(db)} == {"CONFIRMED", "NEEDS_REVIEW"}
    next(button for button in app.button if button.label == "Search").click().run()
    assert app.session_state["active_tab"] == "Search results"
    assert any("Slides: 1" in item.value and "Status: Confirmed" in item.value for item in app.caption)

    tab(app, "Needs Review")
    next(field for field in app.text_input if field.label == "Services").set_value("")
    next(button for button in app.button if button.label == "Save metadata").click().run()
    assert not app.exception
    assert app.session_state["active_tab"] == "Needs Review"
    tab(app, "All cases")
    assert set(app.dataframe[0].value["Status"]) == {"Needs Review"}
    assert next(row for row in list_cases(db) if row["case_id"] == target_id)["services"] is None


def test_ui_import_failure_is_safe_and_other_file_still_imports(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    uploads = []
    for filename in ("source_a.pptx", "source_b.pptx"):
        upload = BytesIO((root / "evaluation/fixtures" / filename).read_bytes())
        upload.name = filename
        uploads.append(upload)
    calls = 0

    def fail_first_parse(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("Bearer SECRET_AUTH private-internal-detail")
        return parse_pptx(path)

    monkeypatch.setattr("app.config.DB_PATH", db)
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: uploads)
    monkeypatch.setattr("app.services.ingest_service.parse_pptx", fail_first_parse)
    app = AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    next(button for button in app.button if button.label == "Parse and save decks").click().run()
    finish_imports(app, db)
    assert not app.exception
    assert len(app.error) == 1
    assert "could not be processed" in app.error[0].value
    assert "SECRET_AUTH" not in app.error[0].value and "private-internal-detail" not in app.error[0].value
    rows = list_cases(db)
    assert len(rows) == 1 and rows[0]["source_deck"] == "source_b.pptx"
    assert len(list(tmp_path.glob("uploads/*.pptx"))) == 1


def test_ui_is_lazy_paginated_and_keeps_selection_and_draft_during_background_work(tmp_path, monkeypatch,deck_variant):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH", db)
    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page", lambda url, **kwargs: {})
    fixture = root / "evaluation/fixtures/source_a.pptx"
    for number in range(15):
        ingest_deck(deck_variant(fixture,f"version-{number}.pptx"), db)
    from app.db.repository import list_cases as repository_list
    reads = Mock(wraps=repository_list)
    monkeypatch.setattr("app.db.repository.list_cases", reads)
    app = AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    reads.assert_not_called()
    tab(app, "All cases")
    reads.assert_called_once_with(db, 25, 0)
    assert len(app.dataframe[0].value) == 25
    tab(app, "Search results")
    next(field for field in app.text_area if field.label == "Search cases").set_value("SYNTHETIC: SAME NAME")
    next(button for button in app.button if button.label == "Search").click().run()
    chosen = next(field for field in app.checkbox if field.label=="Select case")
    target = chosen.key.removeprefix("case-select:")
    chosen.check().run()
    app.number_input(key="partial_match_page").set_value(2).run()
    assert app.session_state["selected_case_ids"] == (target,)
    tab(app,"Selected cases")
    tab(app,"Search results")
    assert app.session_state["selected_case_ids"] == (target,)

    entered, release = Event(), Event()

    def slow_page(url, **kwargs):
        entered.set()
        assert release.wait(20)
        return {"text": "New evidence", "client": "Web client", "services": None}

    monkeypatch.setattr("app.services.ingest_service.fetch_case_page", slow_page)
    manager = get_task_manager(db)
    task_id = manager.submit_import(fixture, "pending.pptx")
    try:
        assert entered.wait(5)
        app.run()
        next(field for field in app.text_area if field.label == "Search cases").set_value("SYNTHETIC: SAME NAME")
        next(button for button in app.button if button.label == "Search").click().run()
        assert not app.exception
        assert manager.get_task(task_id)["status"] == "RUNNING"
        assert len(app.session_state["search_results"]) == 32
        tab(app, "Needs Review")
        assert not app.number_input
        assert len(next(field for field in app.selectbox if field.label == "Choose a case").options) == 32
        field = next(field for field in app.text_input if field.label == "Client")
        field.set_value("Unsaved draft").run()
        selected = app.session_state["_metadata_case"]
        release.set()
        assert manager.wait(task_id)["status"] == "SUCCEEDED"
        app.run()
        assert not app.exception
        assert app.session_state["active_tab"] == "Needs Review"
        assert app.session_state["_metadata_case"] == selected
        assert next(field for field in app.text_input if field.label == "Client").value == "Unsaved draft"
    finally:
        release.set()


def test_ui_auto_confirms_ai_and_has_a_compact_unpaginated_editor(tmp_path, monkeypatch):
    from app.db.repository import apply_ai_inferences, get_field_metadata, get_case
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH",db)
    monkeypatch.setattr("streamlit.file_uploader",lambda *args, **kwargs: [])
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda url, **kwargs: {})
    imported = ingest_deck(root / "evaluation/fixtures/source_a.pptx",db)
    case_id = imported["cases"][0].case_id
    apply_ai_inferences(db,[{"case_id":case_id,"field_name":name,"value":value,
                            "evidence":"Synthetic test brief.","model":"test-model","prompt_version":"v1","cache_key":"cache"}
                           for name,value in (("client","Fixture client"),("services","Filming"))])
    app = AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    next(field for field in app.text_area if field.label == "Search cases").set_value("SYNTHETIC: SAME NAME")
    next(button for button in app.button if button.label == "Search").click().run()
    assert not app.exception and not app.warning
    assert any("Slides: 1" in caption.value and "Status: Confirmed" in caption.value for caption in app.caption)
    tab(app,"All cases")
    assert "Fixture client" in set(app.dataframe[0].value["Client"])
    tab(app,"Needs Review")
    assert not app.number_input
    choices = next(field for field in app.selectbox if field.label == "Choose a case")
    assert len(choices.options) == 1 and "slide 2" in choices.options[0]
    assert not {"Verify current field","Approve case","Reopen for review"} & {button.label for button in app.button}
    assert not {"Source","Verification status","Field to update"} & {field.label for field in app.selectbox}
    next(field for field in app.checkbox if field.label == "Show all cases").check().run()
    next(field for field in app.selectbox if field.label == "Choose a case").select(case_id).run()
    assert next(button for button in app.button if button.label == "Retry AI enrichment").disabled
    next(field for field in app.text_input if field.label == "Client").set_value("Corrected client")
    next(button for button in app.button if button.label == "Save metadata").click().run()
    assert not app.exception and app.session_state["active_tab"] == "Needs Review"
    assert app.session_state["_metadata_case"] == case_id
    assert get_case(db,case_id)["review_status"] == "CONFIRMED"
    services = next(row for row in get_field_metadata(db,case_id) if row["field_name"] == "services")
    assert services["source_kind"] == "AI_INFERRED" and services["verification_status"] == "UNVERIFIED"
    next(field for field in app.text_area if field.label == "Brief").set_value("")
    next(button for button in app.button if button.label == "Save metadata").click().run()
    assert not app.exception and get_case(db,case_id)["review_status"] == "NEEDS_REVIEW"


def test_ui_uses_one_free_text_form_and_reuses_results_for_local_interactions(tmp_path,monkeypatch):
    from app.search.hybrid_search import SearchResults
    from app.search.intent import SearchIntent
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH",db)
    monkeypatch.setattr("streamlit.file_uploader",lambda *args,**kwargs:[])
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda url,**kwargs:{})
    ingest_deck(root / "evaluation/fixtures/source_a.pptx",db)
    rows = list_cases(db)
    for index,row in enumerate(rows):
        row.update(match_level="High Match" if index==0 else "Partial Match",match_reason="Original evidence explains the decision.",
                   evidence=[{"source":"Original slide","quote":"Synthetic test brief."}],uncertainties=[])
    rows.append(dict(rows[0],case_id="rejected-case",title="REJECTED CASE MUST BE HIDDEN",match_level="Not a Match"))
    parsed = SearchIntent(summary="Find the requested film",keywords=["film"],mandatory=[],preferred=[],excluded=[],unresolved_mandatory=[])
    search = Mock(return_value=SearchResults(rows,mode="hybrid",intent=parsed))
    monkeypatch.setattr("app.search.keyword_search.search_cases",search)
    app = AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    next(field for field in app.text_area if field.label=="Search cases").set_value("Find an AI hotel film with real evidence")
    next(button for button in app.button if button.label=="Search").click().run()
    assert not app.exception
    assert not app.slider
    assert any("High Match" in item.value for item in app.markdown)
    assert not any(item.label=="Not a Match" for item in app.tabs)
    assert not any(item.value=="REJECTED CASE MUST BE HIDDEN" for item in app.subheader)
    partial = next(item for item in app.expander if item.label=="Partial Match (1)")
    assert partial.proto.expanded is False
    assert any(item.value==rows[1]["title"] for item in partial.subheader)
    assert not any(item.label=="Search readiness" for item in app.expander)
    assert any(item.value=="Synthetic test brief." for item in app.text)
    assert not any("similarity" in item.value.lower() for item in app.caption)
    app.run()
    assert not app.exception and search.call_count==1
    assert any(item["match_level"]=="Not a Match" for item in app.session_state["search_results"])


def test_ui_export_requires_confirmation_reuses_task_and_defers_file_reads(tmp_path,monkeypatch):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "cases.sqlite3"
    monkeypatch.setattr("app.config.DB_PATH",db)
    monkeypatch.setattr("streamlit.file_uploader",lambda *args,**kwargs:[])
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda *args,**kwargs:{})
    imported = ingest_deck(root / "evaluation/fixtures/source_a.pptx",db)
    ids = tuple(case.case_id for case in reversed(imported["cases"]))
    reads = Mock(side_effect=AssertionError("PPTX must not be read while rendering"))
    monkeypatch.setattr("app.ui.export_panel.download_bytes",reads)
    app = AppTest.from_file(str(root / "app/ui/streamlit_app.py")).run(timeout=30)
    app.session_state["selected_case_ids"] = ids
    tab(app,"Selected cases")
    assert next(button for button in app.button if button.label=="Generate PPTX").disabled
    assert all(item.label!="Export" for item in app.tabs)
    next(button for button in app.button if button.label=="Confirm order").click().run()
    assert not next(button for button in app.button if button.label=="Generate PPTX").disabled
    assert not any(item.label=="Slide order" for item in app.expander)
    next(button for button in app.button if button.label=="Generate PPTX").click().run()
    manager = get_task_manager(db)
    task_id = app.session_state["last_export_task_id"]
    assert manager.wait(task_id)["status"]=="SUCCEEDED"
    list_tasks=manager.list_tasks
    calls=0
    def completing_tasks(owner):
        nonlocal calls
        calls+=1
        jobs=list_tasks(owner)
        # Simulate completion between the sidebar's two status reads.
        return [{**job,"status":"RUNNING"} if job["task_id"]==task_id else job for job in jobs] if calls==1 else jobs
    with monkeypatch.context() as completion:
        completion.setattr(manager,"list_tasks",completing_tasks)
        app.run()
    assert not app.exception and len(app.get("download_button"))==1
    assert app.session_state["active_tab"]=="Selected cases"
    assert not any(item.label=="Slides in this file" for item in app.expander)
    reads.assert_not_called()
    next(button for button in app.button if button.label=="Generate PPTX").click().run()
    assert app.session_state["last_export_task_id"]==task_id
    app.session_state["selected_case_ids"] = ids[:1]
    app.session_state["confirmed_case_ids"] = None
    app.run()
    assert len(app.get("download_button"))==1
    assert any("Your current selection is different" in item.value for item in app.info)
    assert any(item.label=="Slides in this file" for item in app.expander)
    assert next(button for button in app.button if button.label=="Generate PPTX").disabled
    app.session_state["active_tab"] = "Export"
    app.run()
    assert not app.exception and app.session_state["active_tab"]=="Selected cases"
    reads.assert_not_called()


