"""Company wrapper ordering, cache integrity and embedded-font preservation."""
import hashlib
from pathlib import Path
from shutil import copyfile
from zipfile import ZipFile

from lxml import etree as ET
from pptx import Presentation
from pptx.util import Inches
import pytest

from app import config
from app.export import slide_export
from app.export.openxml_export import (
    CT, P, R, REL, OriginalSlideExportError, SlideReference, _resolve, export_slides,
)
from app.services.ingest_service import ingest_deck


def assert_relationships_resolve(path):
    with ZipFile(path) as package:
        names = set(package.namelist())
        for name in names:
            if name.endswith(".rels"):
                part = "" if name == "_rels/.rels" else name.replace("/_rels/", "/")[:-5]
                for rel in ET.fromstring(package.read(name)):
                    if rel.get("TargetMode") != "External":
                        assert _resolve(part,rel.get("Target")) in names


@pytest.fixture
def case_deck(tmp_path):
    deck = Presentation()
    for page in range(2):
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        lines = ["CASE STUDY SYNTHETIC", f"SAME CASE {page}",
                 "BRIEF\nA client wanted a film.\nCREATIVE\nWe made an original film.\nIMPACT\nReached 2 million viewers."]
        for top, text in enumerate(lines):
            slide.shapes.add_textbox(Inches(1),Inches(top),Inches(8),Inches(2)).text = text
    path = tmp_path / "cases.pptx"
    deck.save(path)
    return path


@pytest.fixture
def library(tmp_path, monkeypatch, case_deck):
    monkeypatch.setattr("app.services.ingest_service.fetch_case_page",lambda *args,**kwargs:{})
    db = tmp_path / "cases.sqlite3"
    imported = ingest_deck(case_deck,db)
    return db, imported


def test_template_wraps_custom_order_and_never_changes_original_files(library,tmp_path):
    db, imported = library
    ids = [case.case_id for case in reversed(imported["cases"])]
    paths = [config.EXPORT_TEMPLATE_PATH,Path(imported["source"].source_snapshot_reference)]
    fingerprints = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    plan = slide_export.prepare_export(db,ids)
    output = slide_export.export_original_slides(plan,tmp_path / "wrapped.pptx")
    template, source = [Presentation(path) for path in paths]
    result = Presentation(output)
    expected = [*list(template.slides)[:4],source.slides[1],source.slides[0],*list(template.slides)[4:]]
    assert len(result.slides)==plan["slide_count"]==7
    assert [ET.tostring(slide._element) for slide in result.slides]==[ET.tostring(slide._element) for slide in expected]
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]==fingerprints
    assert_relationships_resolve(output)


def test_template_change_invalidates_cache_and_in_flight_plan(library,tmp_path,monkeypatch):
    db, imported = library
    path = tmp_path / "template.pptx"
    copyfile(config.EXPORT_TEMPLATE_PATH,path)
    monkeypatch.setattr(config,"EXPORT_TEMPLATE_PATH",path)
    ids = [imported["cases"][0].case_id]
    plan = slide_export.prepare_export(db,ids)
    with ZipFile(path,"a") as package:
        package.comment = b"Updated company template"
    refreshed = slide_export.prepare_export(db,ids)
    assert refreshed["cache_key"] != plan["cache_key"]
    with pytest.raises(slide_export.CaseExportError) as error:
        slide_export.export_original_slides(plan,tmp_path / "outdated.pptx")
    assert error.value.code=="EXPORT_TEMPLATE_CHANGED"
    assert not (tmp_path / "outdated.pptx").exists()


@pytest.mark.parametrize("kind",["missing","corrupt","short"])
def test_missing_or_invalid_template_blocks_export(library,tmp_path,monkeypatch,kind):
    path = tmp_path / "invalid.pptx"
    if kind=="corrupt":
        path.write_bytes(b"not a pptx")
    elif kind=="short":
        deck = Presentation()
        for _ in range(4):
            deck.slides.add_slide(deck.slide_layouts[6])
        deck.save(path)
    monkeypatch.setattr(config,"EXPORT_TEMPLATE_PATH",path)
    db, imported = library
    with pytest.raises(slide_export.CaseExportError) as error:
        slide_export.prepare_export(db,[imported["cases"][0].case_id])
    assert error.value.code=="EXPORT_TEMPLATE_INVALID"


def embedded_fonts(source, output, faces):
    """Add tiny synthetic font parts to exercise package copying, without real fonts."""
    with ZipFile(source) as package:
        parts = {name:package.read(name) for name in package.namelist()}
    main = ET.fromstring(parts["ppt/presentation.xml"])
    rels = ET.fromstring(parts["ppt/_rels/presentation.xml.rels"])
    types = ET.fromstring(parts["[Content_Types].xml"])
    fonts = ET.SubElement(main,f"{{{P}}}embeddedFontLst")
    entry = ET.SubElement(fonts,f"{{{P}}}embeddedFont")
    ET.SubElement(entry,f"{{{P}}}font",typeface="Synthetic Font")
    for index,(style,payload) in enumerate(faces.items(),1):
        rid = f"font{index}"
        name = f"ppt/fonts/font{index}.fntdata"
        parts[name] = payload
        ET.SubElement(entry,f"{{{P}}}{style}",**{f"{{{R}}}id":rid})
        ET.SubElement(rels,f"{{{REL}}}Relationship",Id=rid,Type=R+"/font",Target=f"fonts/font{index}.fntdata")
        ET.SubElement(types,f"{{{CT}}}Override",PartName="/"+name,ContentType="application/x-fontdata")
    parts.update({"ppt/presentation.xml":ET.tostring(main),"ppt/_rels/presentation.xml.rels":ET.tostring(rels),
                  "[Content_Types].xml":ET.tostring(types)})
    with ZipFile(output,"w") as package:
        for name,payload in parts.items():
            package.writestr(name,payload)
    return output


def test_matching_embedded_faces_are_reused_and_missing_faces_added_in_schema_order(tmp_path,case_deck):
    first = embedded_fonts(case_deck,tmp_path / "first.pptx",{"regular":b"same-regular","italic":b"same-italic"})
    second = embedded_fonts(case_deck,tmp_path / "second.pptx",
                            {"regular":b"same-regular","bold":b"new-bold","italic":b"same-italic"})
    refs = [SlideReference(path,Presentation(path).slides[0].slide_id) for path in (first,second)]
    output = export_slides(refs,tmp_path / "merged.pptx")
    with ZipFile(output) as package:
        main = ET.fromstring(package.read("ppt/presentation.xml"))
        entry = main.find(f"{{{P}}}embeddedFontLst/{{{P}}}embeddedFont")
        assert [ET.QName(node).localname for node in entry]==["font","regular","bold","italic"]
        assert len([name for name in package.namelist() if name.startswith("ppt/fonts/")])==3
    assert_relationships_resolve(output)
    with ZipFile(second) as package:
        original = ET.fromstring(package.read("ppt/presentation.xml"))
        assert len(original.findall(f"{{{P}}}embeddedFontLst/{{{P}}}embeddedFont"))==1


def test_conflicting_embedded_face_is_not_silently_replaced(tmp_path,case_deck):
    first = embedded_fonts(case_deck,tmp_path / "first.pptx",{"regular":b"first-font-version"})
    second = embedded_fonts(case_deck,tmp_path / "second.pptx",{"regular":b"different-font-version"})
    refs = [SlideReference(path,Presentation(path).slides[0].slide_id) for path in (first,second)]
    with pytest.raises(OriginalSlideExportError) as error:
        export_slides(refs,tmp_path / "conflicting.pptx")
    assert error.value.code=="EXPORT_EMBEDDED_FONTS"
    assert not (tmp_path / "conflicting.pptx").exists()
