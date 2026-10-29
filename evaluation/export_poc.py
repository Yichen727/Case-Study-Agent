"""Build reviewable phase-2 PPTX samples without modifying source decks."""
import json
from pathlib import Path
import posixpath
from time import perf_counter
from zipfile import ZipFile

from lxml import etree as ET
from pptx import Presentation

from app.export.openxml_export import export_slides, SlideReference

ROOT = Path(__file__).resolve().parents[1]
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def source_slides(path):
    with ZipFile(path) as package:
        main = ET.fromstring(package.read("ppt/presentation.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))}
        return [
            (int(node.get("id")), posixpath.normpath("ppt/" + rels[node.get(f"{{{R}}}id")]))
            for node in main.findall(f"{{{P}}}sldIdLst/{{{P}}}sldId")
        ]


def verify_package(path):
    with ZipFile(path) as package:
        names = set(package.namelist())
        for name in names:
            if not name.endswith(".rels"):
                continue
            base = "" if name == "_rels/.rels" else name.replace("/_rels/", "/")[:-5]
            for rel in ET.fromstring(package.read(name)):
                if rel.get("TargetMode") != "External":
                    target = rel.get("Target")
                    resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(base), target))
                    assert resolved in names, (name, target)


def main():
    company = ROOT.parent / "Atomized Studios Creds Deck 2025.pptx"
    a, b = ROOT / "evaluation/fixtures/source_a.pptx", ROOT / "evaluation/fixtures/source_b.pptx"
    folder = ROOT / "evaluation/artifacts/stage2"
    folder.mkdir(parents=True, exist_ok=True)
    cases = {
        "company_subset": [(company, page) for page in (80, 19, 26, 31, 32, 64, 65, 79)],
        "mixed_sources": [(b, 1), (a, 2), (a, 1)],
    }
    source_index = {path: source_slides(path) for path in (company, a, b)}
    report = {"scope": "OOXML package and content checks; PowerPoint rendering is recorded separately", "presentations": []}
    for name, selection in cases.items():
        refs = [SlideReference(path, source_index[path][page - 1][0]) for path, page in selection]
        output = folder / f"{name}.pptx"
        started = perf_counter()
        export_slides(refs, output)
        duration = round(perf_counter() - started, 3)
        verify_package(output)
        parsed = Presentation(output)
        assert len(parsed.slides) == len(selection)
        output_parts = source_slides(output)
        with ZipFile(output) as exported:
            for index, (path, page) in enumerate(selection):
                with ZipFile(path) as source:
                    original = source.read(source_index[path][page - 1][1])
                    copied = exported.read(output_parts[index][1])
                    assert original == copied
        report["presentations"].append({
            "name": name, "output_path": str(output), "bytes": output.stat().st_size,
            "export_seconds": duration, "slide_xml_byte_identical": True, "all_internal_relationships_resolve": True,
            "slide_count": len(parsed.slides), "master_count": len(parsed.slide_masters),
            "slides": [{"source_path": str(path), "source_page": page, "output_page": index}
                       for index, (path, page) in enumerate(selection, 1)],
        })
    (ROOT / "evaluation/stage2_export.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
