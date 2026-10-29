from pathlib import Path
from threading import Event
import json
from unittest.mock import Mock
from zipfile import ZipFile

from lxml import etree as ET
from pptx import Presentation
import pytest

from app.db.repository import connect, update_case_metadata
from app.export import slide_export
from app.export.openxml_export import export_slides, SlideReference
from app.services.ingest_service import ingest_deck
from app.services.task_service import TaskManager
from evaluation.export_poc import verify_package as assert_all_relationships_resolve

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation/fixtures"


@pytest.fixture
def library(tmp_path,monkeypatch):
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda *args,**kwargs:{})
    db = tmp_path / "cases.sqlite3"
    a = ingest_deck(FIXTURES / "source_a.pptx",db)
    b = ingest_deck(FIXTURES / "source_b.pptx",db)
    return db,a,b


def test_background_export_freezes_order_preserves_original_and_reuses_cache(library,monkeypatch):
    db,a,b = library
    ids = [b["cases"][0].case_id,a["cases"][1].case_id,a["cases"][0].case_id]
    order = list(ids)
    entered,release = Event(),Event()
    original = slide_export.export_original_slides

    def held(plan,path,**kwargs):
        entered.set()
        assert release.wait(10)
        return original(plan,path,**kwargs)

    export = Mock(side_effect=held)
    monkeypatch.setattr("app.services.task_service.export_original_slides",export)
    manager = TaskManager(db)
    try:
        task_id = manager.submit_export(order)
        assert entered.wait(5)
        assert manager.submit_export(order)==task_id
        order.reverse()
        update_case_metadata(db,ids[0],{"brief":"Human metadata must not rewrite original slide text."})
        assert json.loads(manager.get_task(task_id)["payload_json"])["case_ids"]==ids
        release.set()
        task = manager.wait(task_id)
        assert task["status"]=="SUCCEEDED"
        output = Path(json.loads(task["result_json"])["output_path"])
        result = Presentation(output)
        sources = (Presentation(FIXTURES / "source_b.pptx").slides[0],Presentation(FIXTURES / "source_a.pptx").slides[1],Presentation(FIXTURES / "source_a.pptx").slides[0])
        for actual,expected in zip(result.slides,sources):
            assert ET.tostring(actual._element)==ET.tostring(expected._element)
        assert len(result.slides)==3
        assert_all_relationships_resolve(output)
        assert manager.submit_export(ids)==task_id and export.call_count==1
        other = manager.submit_export(order)
        assert other!=task_id and manager.wait(other)["status"]=="SUCCEEDED"
        other_path = Path(json.loads(manager.get_task(other)["result_json"])["output_path"])
        assert other_path!=output and other_path.is_file()
        manager.clear_finished()
        assert not output.exists() and not other_path.exists()
        assert Path(a["source"].source_snapshot_reference).is_file()
    finally:
        release.set()
        manager.shutdown()


def test_missing_or_changed_source_does_not_create_an_invalid_export(library):
    db,a,_ = library
    ids = [a["cases"][0].case_id]
    with pytest.raises(slide_export.CaseExportError,match="no longer available"):
        slide_export.prepare_export(db,["not-a-case"])
    with pytest.raises(slide_export.CaseExportError):
        slide_export.prepare_export(db,[*ids,*ids])
    plan = slide_export.prepare_export(db,ids)
    snapshot = Path(a["source"].source_snapshot_reference)
    with ZipFile(snapshot,"a") as package:
        package.comment = b"Changed snapshot"
    manager = TaskManager(db)
    try:
        task = manager.wait(manager.submit_export(ids))
        assert task["status"]=="FAILED" and task["error_code"]=="EXPORT_SOURCE_CHANGED"
        assert list((db.parent / "exports").glob("*.pptx"))==[]
    finally:
        manager.shutdown()
    snapshot.unlink()
    with pytest.raises(slide_export.CaseExportError,match="Re-import"):
        slide_export.prepare_export(db,ids)


def test_export_cache_identity_includes_engine_version(library,monkeypatch):
    db,a,_ = library
    ids = [a["cases"][0].case_id]
    plan = slide_export.prepare_export(db,ids)
    monkeypatch.setattr(slide_export,"EXPORT_ENGINE_VERSION","next-engine")
    assert slide_export.prepare_export(db,ids)["cache_key"]!=plan["cache_key"]
    with pytest.raises(slide_export.CaseExportError,match="updated"):
        slide_export.export_original_slides(plan,db.parent / "no-output.pptx")


def test_duplicate_slide_occurrences_own_distinct_notes_parts(tmp_path):
    source = tmp_path / "notes.pptx"
    original = Presentation(FIXTURES / "source_a.pptx")
    original.slides[0].notes_slide.notes_text_frame.text = "Keep the original presenter notes."
    original.save(source)
    slide_id = original.slides[0].slide_id
    output = export_slides([SlideReference(source,slide_id),SlideReference(source,slide_id)],tmp_path / "duplicates.pptx")
    result = Presentation(output)
    first,second = result.slides
    assert first.part.partname!=second.part.partname
    assert first.notes_slide.part.partname!=second.notes_slide.part.partname
    for slide in result.slides:
        assert slide.notes_slide.notes_text_frame.text=="Keep the original presenter notes."
        back = next(rel for rel in slide.notes_slide.part.rels.values() if rel.reltype.endswith("/slide"))
        assert back.target_part is slide.part
        assert ET.tostring(slide._element)==ET.tostring(original.slides[0]._element)
    assert_all_relationships_resolve(output)


def test_local_and_cloud_cases_can_export_the_same_snapshot_page_independently(library):
    db,a,_ = library
    cloud = ingest_deck(FIXTURES / "source_a.pptx",db,source_type="SHAREPOINT",
                        original_source_url="https://example.sharepoint.com/authorized-file")
    ids = [a["cases"][0].case_id,cloud["cases"][0].case_id]
    plan = slide_export.prepare_export(db,ids)
    assert plan["slides"][0]["path"]==plan["slides"][1]["path"]
    output = slide_export.export_original_slides(plan,db.parent / "two-imports.pptx")
    slides = Presentation(output).slides
    assert len(slides)==2 and slides[0].part.partname!=slides[1].part.partname
    assert_all_relationships_resolve(output)
