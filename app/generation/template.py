"""Template-specific text fill PoC; no UI, database, rendering or media generation."""
from copy import deepcopy
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree as E

from app import config
from app.generation.models import Content

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
SLIDE_PART = "ppt/slides/slide1.xml"
MAPPING = {"SECTION": "5", "TITLE": "6", "BODY COPY": "3", "PAGE NUMBER": "2"}
LABELS = ("BRIEF", "CREATIVE", "IMPACT")


class TemplateError(ValueError):
    pass


def _xml(data):
    return E.fromstring(data, E.XMLParser(resolve_entities=False, no_network=True))


def _text(element):
    return "".join(element.xpath(".//a:t/text()", namespaces=NS))


def _load(package):
    presentation = _xml(package.read("ppt/presentation.xml"))
    if len(presentation.findall("p:sldIdLst/p:sldId", NS)) != 1:
        raise TemplateError("The single-case template must contain exactly one slide.")
    size = presentation.find("p:sldSz", NS)
    if size is None or (size.get("cx"), size.get("cy")) != ("12192000", "6858000"):
        raise TemplateError("The single-case template has an unexpected slide size.")
    root = _xml(package.read(SLIDE_PART))
    shapes = {}
    for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
        identity = shape.find("p:nvSpPr/p:cNvPr", NS)
        if identity is not None and identity.get("name") in MAPPING:
            name = identity.get("name")
            if name in shapes or identity.get("id") != MAPPING[name] or shape.find("p:txBody", NS) is None:
                raise TemplateError("The single-case template shapes have changed.")
            shapes[name] = shape
    if set(shapes) != set(MAPPING):
        raise TemplateError("The single-case template is missing a required text shape.")
    paragraphs = shapes["BODY COPY"].findall("p:txBody/a:p", NS)
    labels = [_text(p) for p in paragraphs if _text(p) in LABELS]
    if labels != list(LABELS):
        raise TemplateError("The single-case template must contain Brief, Creative and Impact in order.")
    media = root.xpath(".//p:sp[p:txBody//a:t='REPLACE WITH VIDEO' or p:txBody//a:t='REPLACE WITH IMAGE']", namespaces=NS)
    if len(media) != 4:
        raise TemplateError("The single-case template media placeholders have changed.")
    return root, shapes, paragraphs, media


def _set_run_text(paragraph, text):
    runs = paragraph.findall("a:r", NS)
    if not runs:
        raise TemplateError("The single-case template text style is missing.")
    runs[0].find("a:t", NS).text = text
    for run in runs[1:]:
        paragraph.remove(run)


def _spacing(paragraph, line, before=0, after=0):
    properties = paragraph.find("a:pPr", NS)
    if properties is None:
        properties = E.Element(f"{{{NS['a']}}}pPr")
        paragraph.insert(0, properties)
    spacing = (("lnSpc", line), ("spcBef", before), ("spcAft", after))
    for name, _ in spacing:
        old = properties.find(f"a:{name}", NS)
        if old is not None:
            properties.remove(old)
    for index, (name, points) in enumerate(spacing):
        element = E.Element(f"{{{NS['a']}}}{name}")
        E.SubElement(element, f"{{{NS['a']}}}spcPts", val=str(points * 100))
        properties.insert(index, element)


def fill_template(content: Content, output_path, *, template_path=None, remove_thumbnail=False):
    """Write a new one-slide sample. Only its slide XML is changed."""
    template = Path(template_path or config.SINGLE_CASE_TEMPLATE_PATH).resolve()
    output = Path(output_path).resolve()
    if output == template or output.exists():
        raise TemplateError("Use a new output path; existing files cannot be overwritten.")
    if content.body_words > 250 or not content.title or not any((content.brief, content.creative, content.impact)):
        raise TemplateError("Provide a title and body text, with no more than 250 total body words.")
    try:
        with ZipFile(template) as source:
            root, shapes, paragraphs, media = _load(source)
            _set_run_text(shapes["TITLE"].find("p:txBody/a:p", NS), content.title)
            section = shapes["SECTION"].findall("p:txBody/a:p/a:r/a:t", NS)
            if len(section) != 2:
                raise TemplateError("The template pillar text style has changed.")
            section[1].text = content.pillar or ""
            body = shapes["BODY COPY"].find("p:txBody", NS)
            for paragraph in paragraphs:
                body.remove(paragraph)
            for index, field in enumerate(("brief", "creative", "impact")):
                heading_index = next(i for i, p in enumerate(paragraphs) if _text(p) == LABELS[index])
                heading = deepcopy(paragraphs[heading_index])
                original = next(p for p in paragraphs[heading_index + 1:] if _text(p).startswith("("))
                text = deepcopy(original)
                _set_run_text(text, getattr(content, field) or "")
                # Leave one body-line of space before each subsequent section.
                _spacing(heading, 14, before=12 if index else 0, after=2)
                _spacing(text, 12)
                body.extend((heading, text))
            for shape in media:
                for text in shape.findall(".//a:t", NS):
                    text.text = ""
            slide_xml = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            created = False
            try:
                with ZipFile(output, "x") as target:
                    created = True
                    for info in source.infolist():
                        if remove_thumbnail and info.filename == "docProps/thumbnail.jpeg":
                            continue
                        data = slide_xml if info.filename == SLIDE_PART else source.read(info.filename)
                        if remove_thumbnail and info.filename == "_rels/.rels":
                            relationships = _xml(data)
                            for relationship in list(relationships):
                                if relationship.get("Type", "").endswith("/metadata/thumbnail"):
                                    relationships.remove(relationship)
                            data = E.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
                        if remove_thumbnail and info.filename == "[Content_Types].xml":
                            types = _xml(data)
                            for element in list(types):
                                if element.get("PartName") == "/docProps/thumbnail.jpeg":
                                    types.remove(element)
                            data = E.tostring(types, xml_declaration=True, encoding="UTF-8", standalone=True)
                        target.writestr(info, data)
            except Exception:
                if created:
                    output.unlink(missing_ok=True)
                raise
    except (BadZipFile, KeyError, E.XMLSyntaxError):
        raise TemplateError("The single-case template is not a valid supported PPTX.") from None
    except OSError:
        raise TemplateError("The template could not be read or the sample could not be saved.") from None
    return output
