"""Replace one supported body in a derived slide XML; retain all source relationships."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from lxml import etree as E

from app.ingestion.pptx_parser import _link_type
from app.export.openxml_export import OriginalSlideExportError, _xml, _bytes

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
LABELS = {"BRIEF", "CREATIVE", "IMPACT"}


def _text(node):
    return "".join(node.xpath(".//a:t/text()", namespaces=NS)).strip()


def replace_body(slide_xml, edit):
    root = _xml(slide_xml)
    matches = root.xpath(".//p:sp[p:nvSpPr/p:cNvPr/@id=$shape_id]",
                         namespaces=NS, shape_id=str(edit["body_shape_id"]))
    if len(matches) != 1:
        raise OriginalSlideExportError("The edited body is no longer supported.", "EXPORT_EDIT_INVALID")
    body = matches[0].find("p:txBody", NS)
    if body is None:
        raise OriginalSlideExportError("The edited body is missing.", "EXPORT_EDIT_INVALID")
    originals = body.findall("a:p", NS)
    styles, footers = {}, []
    for paragraph in originals:
        if _link_type(_text(paragraph)) in {"READ_MORE", "WATCH_VIDEO"}:
            footers.append(deepcopy(paragraph))
            continue
        if paragraph.findall(".//a:hlinkClick", NS) or paragraph.findall(".//a:hlinkMouseOver", NS):
            raise OriginalSlideExportError("Inline links cannot be edited safely.", "EXPORT_EDIT_UNSUPPORTED")
        for run in paragraph.findall("a:r", NS):
            text = _text(run)
            if text:
                role = "HEADING" if text.rstrip(":").upper() in LABELS else "BODY"
                styles.setdefault(role, (paragraph, run))
    if "BODY" not in styles:
        raise OriginalSlideExportError("The body text style is unavailable.", "EXPORT_EDIT_UNSUPPORTED")
    for paragraph in originals:
        body.remove(paragraph)
    for value in edit["paragraphs"]:
        prototype, run_style = styles.get(value["role"], styles["BODY"])
        paragraph = deepcopy(prototype)
        for node in list(paragraph):
            if node.tag not in {f"{{{NS['a']}}}pPr", f"{{{NS['a']}}}endParaRPr"}:
                paragraph.remove(node)
        run = deepcopy(run_style)
        run.find("a:t", NS).text = value["text"]
        if value["role"] == "BODY":
            # The prototype may carry an accent or theme color. Body text is black.
            run_properties = run.find("a:rPr", NS)
            if run_properties is None:
                run_properties = E.Element(f"{{{NS['a']}}}rPr")
                run.insert(0, run_properties)
            run_properties.set("b", "0")
            for child in list(run_properties):
                if E.QName(child).localname in {"noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"}:
                    run_properties.remove(child)
            fill = E.Element(f"{{{NS['a']}}}solidFill")
            E.SubElement(fill, f"{{{NS['a']}}}srgbClr", val="000000")
            run_properties.insert(1 if run_properties.find("a:ln", NS) is not None else 0, fill)
        end = paragraph.find("a:endParaRPr", NS)
        paragraph.insert(list(paragraph).index(end) if end is not None else len(paragraph), run)
        properties = paragraph.find("a:pPr", NS)
        if properties is None:
            properties = E.Element(f"{{{NS['a']}}}pPr")
            paragraph.insert(0, properties)
        if value["role"] == "BODY":
            default_run = properties.find("a:defRPr", NS)
            if default_run is not None:
                default_run.set("b", "0")
            if end is not None:
                end.set("b", "0")
        # Free paragraphs retain the source font/line height and gain a clear gap.
        for name, points in (("spcBef", 0), ("spcAft", 10 if value["role"] == "BODY" else 2)):
            spacing = properties.find(f"a:{name}", NS)
            if spacing is None:
                spacing = E.Element(f"{{{NS['a']}}}{name}")
                preceding = {"lnSpc"} if name == "spcBef" else {"lnSpc", "spcBef"}
                position = next((i for i, child in enumerate(properties)
                                 if E.QName(child).localname not in preceding), len(properties))
                properties.insert(position, spacing)
            for child in list(spacing):
                spacing.remove(child)
            E.SubElement(spacing, f"{{{NS['a']}}}spcPts", val=str(points * 100))
        body.append(paragraph)
    body.extend(footers)
    return _bytes(root)


def save_derived_page(folder, data):
    """Content-addressed local page, not a second copy of its media or source deck."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256(data).hexdigest()}.xml"
    if path.is_file() and path.read_bytes() == data:
        return path
    temporary = folder / f".{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
