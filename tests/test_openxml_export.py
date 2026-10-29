from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET
from pptx import Presentation
import pytest

from app.export.openxml_export import export_slides, OriginalSlideExportError, SlideReference, _resolve

FIXTURES = Path(__file__).resolve().parents[1] / "evaluation" / "fixtures"


def reference(path, page):
    return SlideReference(path, Presentation(path).slides[page - 1].slide_id)


def assert_all_relationships_resolve(path):
    with ZipFile(path) as package:
        names = set(package.namelist())
        for name in names:
            if not name.endswith(".rels"):
                continue
            base = "" if name == "_rels/.rels" else name.replace("/_rels/", "/")[:-5]
            for rel in ET.fromstring(package.read(name)):
                if rel.get("TargetMode") != "External":
                    assert _resolve(base, rel.get("Target")) in names, (name, rel.get("Target"))


def test_export_preserves_order_original_slide_xml_assets_chart_and_masters(tmp_path):
    a, b = FIXTURES / "source_a.pptx", FIXTURES / "source_b.pptx"
    selected = [reference(b, 1), reference(a, 2), reference(a, 1)]
    output = export_slides(selected, tmp_path / "selected.pptx")
    result = Presentation(output)
    assert len(result.slides) == 3
    assert len(result.slide_masters) == 2
    assert any(shape.has_chart for shape in result.slides[2].shapes)
    assert result.slides[2].shapes[-2].chart.series[0].values == (1.0, 2.0)
    assert result.slides[0].slide_layout.slide_master.background.fill.fore_color.rgb == Presentation(b).slide_master.background.fill.fore_color.rgb
    assert_all_relationships_resolve(output)
    for target, original in zip(result.slides, (Presentation(b).slides[0], Presentation(a).slides[1], Presentation(a).slides[0])):
        assert ET.tostring(target._element) == ET.tostring(original._element)
    with ZipFile(a) as source, ZipFile(output) as exported:
        identifiers = []
        main = ET.fromstring(exported.read("ppt/presentation.xml"))
        identifiers.extend(node.get("id") for node in main.findall("{*}sldMasterIdLst/{*}sldMasterId"))
        for part in exported.namelist():
            if part.startswith("ppt/slideMasters/") and part.endswith(".xml"):
                master = ET.fromstring(exported.read(part))
                identifiers.extend(node.get("id") for node in master.findall("{*}sldLayoutIdLst/{*}sldLayoutId"))
        assert len(identifiers) == len(set(identifiers))
        for entry in source.namelist():
            if entry.startswith(("ppt/media/", "ppt/embeddings/", "ppt/charts/")) and not entry.endswith(".rels"):
                assert source.read(entry) in [exported.read(name) for name in exported.namelist() if not name.endswith(".rels")]


def test_same_source_custom_order_and_rejected_different_sizes(tmp_path):
    a = FIXTURES / "source_a.pptx"
    output = export_slides([reference(a, 2), reference(a, 1)], tmp_path / "reordered.pptx")
    result = Presentation(output)
    assert "continuation" in "\n".join(shape.text for shape in result.slides[0].shapes if shape.has_text_frame)
    wide = Presentation(a)
    wide.slide_width = wide.slide_width * 2
    other = tmp_path / "wide.pptx"
    wide.save(other)
    with pytest.raises(OriginalSlideExportError, match="different slide sizes"):
        export_slides([reference(a, 1), reference(other, 1)], tmp_path / "invalid.pptx")
    assert not (tmp_path / "invalid.pptx").exists()
    assert len(Presentation(a).slides) == 2
