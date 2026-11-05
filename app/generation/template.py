"""Fill the approved single-case template while preserving its editable layout."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree as E
from PIL import Image, UnidentifiedImageError

from app import config
from app.generation.models import Content

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
SLIDE_PART = "ppt/slides/slide1.xml"
SLIDE_RELS_PART = "ppt/slides/_rels/slide1.xml.rels"
MAPPING = {"SECTION": "5", "TITLE": "6", "BODY COPY": "3", "PAGE NUMBER": "2"}
LABELS = ("BRIEF", "CREATIVE", "IMPACT")
# Image order follows the visible labels, not the slide's XML shape order.
# Each pair identifies the image rectangle and its separate caption shape.
IMAGE_SLOTS = (("7", "8"), ("9", "19"), ("12", "18"), ("13", "17"))
LINK_LABELS = ("READ MORE HERE.", "WATCH VIDEO HERE.")
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


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
    shapes_by_id = {}
    for shape in root.findall("p:cSld/p:spTree/p:sp", NS):
        identity = shape.find("p:nvSpPr/p:cNvPr", NS)
        if identity is not None:
            shapes_by_id[identity.get("id")] = shape
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
    for label in LABELS:
        if sum(_text(p) == f"({label} CONTEXT HERE)" for p in paragraphs) != 1:
            raise TemplateError("The single-case template body placeholders have changed.")
    if sum(all(label in _text(p) for label in LINK_LABELS) for p in paragraphs) != 1:
        raise TemplateError("The single-case template optional link placeholder has changed.")
    media = []
    for number, (region_id, caption_id) in enumerate(IMAGE_SLOTS, 1):
        region, caption = shapes_by_id.get(region_id), shapes_by_id.get(caption_id)
        if region is None or caption is None or _text(caption) != f"IMAGE {number} HERE":
            raise TemplateError("The single-case template image placeholders have changed.")
        transform = region.find("p:spPr/a:xfrm", NS)
        if transform is None or transform.find("a:off", NS) is None or transform.find("a:ext", NS) is None:
            raise TemplateError("The single-case template image geometry is missing.")
        media.append(caption)
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


def _add_images(root, relationships, images):
    """Place normalized local images over their numbered rectangles, using cover crop."""
    if len(images) > len(IMAGE_SLOTS):
        raise TemplateError("The single-case template supports up to four images.")
    media = {}
    tree = root.find("p:cSld/p:spTree", NS)
    next_shape_id = max(int(value) for value in root.xpath(".//p:cNvPr/@id", namespaces=NS)) + 1
    relationship_ids = {rel.get("Id") for rel in relationships}
    next_relationship = 1
    for index, data in enumerate(images):
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG"}:
                    raise TemplateError("Use PNG or JPEG images for the slide.")
                extension = "png" if image.format == "PNG" else "jpg"
                image_ratio = image.width / image.height
        except (UnidentifiedImageError, OSError):
            raise TemplateError("A saved image could not be read. Please upload it again.") from None
        name = f"case-image-{sha256(data).hexdigest()}.{extension}"
        media[f"ppt/media/{name}"] = data
        while f"rId{next_relationship}" in relationship_ids:
            next_relationship += 1
        relationship_id = f"rId{next_relationship}"
        relationship_ids.add(relationship_id)
        E.SubElement(relationships, f"{{{PACKAGE_REL_NS}}}Relationship", Id=relationship_id,
                     Type=f"{REL_NS}/image", Target=f"../media/{name}")
        region_id = IMAGE_SLOTS[index][0]
        region = root.xpath(f".//p:sp[p:nvSpPr/p:cNvPr/@id='{region_id}']", namespaces=NS)[0]
        transform = region.find("p:spPr/a:xfrm", NS)
        size = transform.find("a:ext", NS)
        ratio = int(size.get("cx")) / int(size.get("cy"))
        horizontal = round((1 - ratio / image_ratio) * 50000) if image_ratio > ratio else 0
        vertical = round((1 - image_ratio / ratio) * 50000) if image_ratio < ratio else 0
        # All interpolated values are internal identifiers or numeric crop values.
        picture = _xml(f'''<p:pic xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="{REL_NS}">
          <p:nvPicPr><p:cNvPr id="{next_shape_id + index}" name="Case image {index + 1}"/>
          <p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>
          <p:blipFill><a:blip r:embed="{relationship_id}"/>
          <a:srcRect l="{horizontal}" r="{horizontal}" t="{vertical}" b="{vertical}"/>
          <a:stretch><a:fillRect/></a:stretch></p:blipFill>
          <p:spPr><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'''.encode())
        picture.find("p:spPr", NS).insert(0, deepcopy(transform))
        tree.insert(tree.index(region) + 1, picture)
    return media


def fill_template(content: Content, output_path, *, template_path=None, remove_thumbnail=False, images=()):
    """Write one slide, clearing sample text and unused sample hyperlink relationships."""
    template = Path(template_path or config.SINGLE_CASE_TEMPLATE_PATH).resolve()
    output = Path(output_path).resolve()
    if output == template or output.exists():
        raise TemplateError("Use a new output path; existing files cannot be overwritten.")
    if content.body_words > 250 or not content.title or not any((content.brief, content.creative, content.impact)):
        raise TemplateError("Provide a title and body text, with no more than 250 total body words.")
    if len(content.export_title) > 40:
        raise TemplateError("Please shorten the title to fit the template before exporting.")
    try:
        with ZipFile(template) as source:
            root, shapes, paragraphs, media = _load(source)
            _set_run_text(shapes["TITLE"].find("p:txBody/a:p", NS), content.export_title)
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
                original = next(p for p in paragraphs[heading_index + 1:]
                                if _text(p) == f"({LABELS[index]} CONTEXT HERE)")
                text = deepcopy(original)
                _set_run_text(text, getattr(content, field) or "")
                # Leave one body-line of space before each subsequent section.
                _spacing(heading, 14, before=12 if index else 0, after=2)
                _spacing(text, 12)
                body.extend((heading, text))
            for shape in media:
                for text in shape.findall(".//a:t", NS):
                    text.text = ""
            relationships = _xml(source.read(SLIDE_RELS_PART))
            image_parts = _add_images(root, relationships, images)
            slide_xml = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            used_relationships = set(root.xpath("//@r:id", namespaces={"r": REL_NS}))
            for relationship in list(relationships):
                if (relationship.get("Type", "").endswith("/hyperlink")
                        and relationship.get("Id") not in used_relationships):
                    relationships.remove(relationship)
            relationships_xml = E.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            created = False
            try:
                with ZipFile(output, "x") as target:
                    created = True
                    for info in source.infolist():
                        if remove_thumbnail and info.filename == "docProps/thumbnail.jpeg":
                            continue
                        data = slide_xml if info.filename == SLIDE_PART else source.read(info.filename)
                        if info.filename == SLIDE_RELS_PART:
                            data = relationships_xml
                        if remove_thumbnail and info.filename == "_rels/.rels":
                            relationships = _xml(data)
                            for relationship in list(relationships):
                                if relationship.get("Type", "").endswith("/metadata/thumbnail"):
                                    relationships.remove(relationship)
                            data = E.tostring(relationships, xml_declaration=True, encoding="UTF-8", standalone=True)
                        if (remove_thumbnail or image_parts) and info.filename == "[Content_Types].xml":
                            types = _xml(data)
                            for element in list(types):
                                if remove_thumbnail and element.get("PartName") == "/docProps/thumbnail.jpeg":
                                    types.remove(element)
                            for extension, content_type in (("png", "image/png"), ("jpg", "image/jpeg")):
                                if any(name.endswith(f".{extension}") for name in image_parts):
                                    default = next((element for element in types
                                                    if element.get("Extension", "").lower() == extension), None)
                                    if default is None:
                                        default = E.SubElement(types, f"{{{E.QName(types).namespace}}}Default")
                                    # The company template has a legacy JPG -> image/.jpg
                                    # entry; normalize it when actually embedding JPEGs.
                                    default.set("Extension", extension)
                                    default.set("ContentType", content_type)
                            data = E.tostring(types, xml_declaration=True, encoding="UTF-8", standalone=True)
                        target.writestr(info, data)
                    for name, data in image_parts.items():
                        target.writestr(name, data)
            except Exception:
                if created:
                    output.unlink(missing_ok=True)
                raise
    except (BadZipFile, KeyError, E.XMLSyntaxError):
        raise TemplateError("The single-case template is not a valid supported PPTX.") from None
    except OSError:
        raise TemplateError("The template could not be read or the sample could not be saved.") from None
    return output
