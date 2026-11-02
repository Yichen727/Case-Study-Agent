"""Offline original-slide export for the Copilot skill; no app, database or network."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from zipfile import BadZipFile, ZipFile

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "Case Study Tool Base Template.pptx"


class ExportInputError(ValueError):
    pass


def load_engine():
    try:
        import openxml_export
    except ModuleNotFoundError as exc:
        if exc.name == "lxml":
            raise ExportInputError("The runtime lacks lxml. This skill cannot install packages; original-slide export is unavailable here.") from None
        raise
    return openxml_export


def inspect_files(paths, pages):
    engine = load_engine()
    results = []
    for supplied in paths:
        path = Path(supplied).resolve()
        with ZipFile(path) as package:
            source = engine._Source(package)
            ids = list(source.slides)
            items = []
            for page in pages:
                if not 1 <= page <= len(ids):
                    raise ExportInputError("A requested original slide number is outside its presentation.")
                slide_id = ids[page - 1]
                root = engine._xml(package.read(source.slides[slide_id]))
                snippets = root.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}t")
                items.append({"slide_number": page, "slide_id": slide_id,
                              "text_preview": " | ".join(node.text or "" for node in snippets)[:500]})
            results.append({"source_file": path.name, "source_path": str(path), "slide_count": len(ids),
                            "slide_size": list(source.size), "slides": items})
    return {"status": "ok", "files": results}


def verify_export(engine, references, output):
    """Check order, unchanged slide XML and all internal package relationships."""
    with ExitStack() as stack:
        exported = stack.enter_context(ZipFile(output))
        result = engine._Source(exported)
        if len(result.slides) != len(references):
            raise ExportInputError("The generated slide count does not match the requested order.")
        sources = {}
        for ref, target_part in zip(references, result.slides.values()):
            path = ref.source_path.resolve()
            if path not in sources:
                package = stack.enter_context(ZipFile(path))
                sources[path] = engine._Source(package)
            source = sources[path]
            if source.zip.read(source.slides[ref.original_slide_id]) != exported.read(target_part):
                raise ExportInputError("An exported slide differs from its original XML.")
        for name in exported.namelist():
            if not name.endswith(".rels"):
                continue
            part = "" if name == "_rels/.rels" else name.replace("/_rels/", "/")[:-5]
            for rel in engine._xml(exported.read(name)):
                if rel.get("TargetMode") != "External" and engine._resolve(part, rel.get("Target")) not in result.names:
                    raise ExportInputError("An exported relationship points to a missing resource.")


def export_selection(selection, template, output):
    engine = load_engine()
    data = json.loads(Path(selection).read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list) or not cases:
        raise ExportInputError("The selection must contain a nonempty cases array.")
    template = Path(template).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ExportInputError("Choose a new output filename; existing files will not be overwritten.")
    template_ids = engine.presentation_slide_ids(template)
    if len(template_ids) < 5:
        raise ExportInputError("The company template must contain at least five pages.")
    indexed = {}
    selected = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("source_file"), str):
            raise ExportInputError("Each case needs an actual runtime source_file path and a slide_number.")
        path = Path(case["source_file"]).resolve()
        page = case.get("slide_number")
        if type(page) is not int or page < 1:
            raise ExportInputError("slide_number must be a positive, one-based integer.")
        if path not in indexed:
            indexed[path] = engine.presentation_slide_ids(path)
        if page > len(indexed[path]):
            raise ExportInputError("A selected original slide number is outside its source presentation.")
        selected.append(engine.SlideReference(path, indexed[path][page - 1]))
    base = [engine.SlideReference(template, slide_id) for slide_id in template_ids]
    references = [*base[:4], *selected, *base[4:]]
    generated = False
    try:
        engine.export_slides(references, output)
        generated = True
        verify_export(engine, references, output)
    except Exception:
        if generated:
            output.unlink(missing_ok=True)
        raise
    return {"status": "ok", "output_path": str(output), "case_count": len(selected),
            "template_slide_count": len(base), "slide_count": len(references),
            "order": "template pages 1-4, selected cases, remaining template pages",
            "checks": {"original_slide_xml_identical": True, "internal_relationships_resolve": True},
            "visual_fidelity": "Not rendered in PowerPoint; inspect the downloaded presentation before sharing."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("probe", help="Check the packaged engine, lxml and template without reading user files.")
    inspect = commands.add_parser("inspect", help="Check actual attached files and optional original page numbers.")
    inspect.add_argument("files", nargs="+")
    inspect.add_argument("--pages", nargs="*", type=int, default=[])
    export = commands.add_parser("export", help="Export an ordered selection into the company template.")
    export.add_argument("--selection", required=True)
    export.add_argument("--template", default=str(TEMPLATE))
    export.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "probe":
            engine = load_engine()
            ids = engine.presentation_slide_ids(TEMPLATE)
            if len(ids) < 5:
                raise ExportInputError("The packaged template must contain at least five slides.")
            result = {"status": "ok", "engine": "original OOXML copying", "lxml_available": True,
                      "template_available": True, "template_slide_count": len(ids)}
        elif args.command == "inspect":
            result = inspect_files(args.files, args.pages)
        else:
            result = export_selection(args.selection, args.template, args.output)
    except (ExportInputError, FileNotFoundError, PermissionError, BadZipFile, json.JSONDecodeError) as exc:
        message = str(exc) if isinstance(exc, ExportInputError) else "An input file is unavailable, unreadable or invalid. Check actual runtime files and the selection JSON."
        print(json.dumps({"status": "error", "message": message}))
        return 2
    except Exception as exc:
        code = getattr(exc, "code", "EXPORT_FAILED")
        messages = {
            "EXPORT_SIZE_MISMATCH": "The template and source slides have different dimensions. Original-slide export cannot rescale them.",
            "EXPORT_EMBEDDED_FONTS": "The presentations embed incompatible versions of the same font.",
            "EXPORT_LINK_TARGET_MISSING": "A selected slide links to another original slide. Include that destination slide or stop the export.",
        }
        print(json.dumps({"status": "error", "code": code,
                          "message": messages.get(code, "The original-slide export could not be completed. No verified download is available.")}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
