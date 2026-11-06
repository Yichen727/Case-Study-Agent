"""Freeze one draft and fill the approved single-slide template locally."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree as E

from app import config
from app.generation.models import Content, DraftImage
from app.generation.images import images_hash
from app.generation.links import DraftLink, link_hash
from app.generation.template import fill_template, TemplateError, NS, SLIDE_PART
from app.ingestion.validation import validate_pptx
from app.ingestion.errors import IngestionError

ENGINE_VERSION = "generated-case-v6-link-colour"
ERROR_MESSAGES = {
    "CASE_EXPORT_INCOMPLETE": "Add a title and at least one body section before generating the slide.",
    "CASE_EXPORT_BODY_TOO_LONG": "Please shorten Brief, Creative and Impact to fit the slide before exporting.",
    "CASE_EXPORT_TITLE_TOO_LONG": "Please shorten the title to fit the template before exporting.",
    "CASE_TEMPLATE_INVALID": "The single-case template is missing or unsupported. Restore it in assets/templates.",
    "CASE_TEMPLATE_CHANGED": "The single-case template changed during generation. Generate the slide again.",
    "CASE_EXPORT_FAILED": "The slide could not be generated. Your saved draft is retained; please try again.",
    "CASE_IMAGE_MISSING": "A saved image is missing or has changed. Remove it and upload it again before exporting.",
}


class GeneratedExportError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(ERROR_MESSAGES[code])


def content_hash(content):
    return sha256(json.dumps(content.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def template_hash():
    try:
        return sha256(config.SINGLE_CASE_TEMPLATE_PATH.read_bytes()).hexdigest()
    except OSError:
        raise GeneratedExportError("CASE_TEMPLATE_INVALID") from None


def prepare_generated_export(draft):
    content = draft.content
    if not content.title or not any((content.brief, content.creative, content.impact)):
        raise GeneratedExportError("CASE_EXPORT_INCOMPLETE")
    if content.body_words > 250:
        raise GeneratedExportError("CASE_EXPORT_BODY_TOO_LONG")
    if len(content.export_title) > 40:
        raise GeneratedExportError("CASE_EXPORT_TITLE_TOO_LONG")
    plan = {"draft_id": draft.draft_id, "revision": draft.revision, "request_count": draft.request_count,
            "content": content.model_dump(),
            "images": [image.model_dump() for image in draft.images], "images_hash": images_hash(draft.images),
            "link": draft.selected_link.model_dump() if draft.selected_link else None,
            "link_hash": link_hash(draft.selected_link),
            "content_hash": content_hash(content), "template_sha256": template_hash(), "engine_version": ENGINE_VERSION}
    plan["cache_key"] = sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    return plan


def export_generated_case(plan, store):
    store.load(plan["draft_id"])
    if plan["template_sha256"] != template_hash():
        raise GeneratedExportError("CASE_TEMPLATE_CHANGED")
    if plan["engine_version"] != ENGINE_VERSION:
        raise GeneratedExportError("CASE_EXPORT_FAILED")
    folder = store.folder(plan["draft_id"]) / "exports"
    output = folder / f"{plan['cache_key']}.pptx"
    temporary = folder / f".{uuid4().hex}.pptx"
    summary = {key: plan[key] for key in ("draft_id", "revision", "request_count", "cache_key", "content_hash", "images_hash", "link_hash", "template_sha256", "engine_version")}
    summary.update(output_path=str(output), slide_count=1)
    try:
        if not output.is_file():
            content = Content(**plan["content"])
            image_data = []
            for value in plan["images"]:
                image = DraftImage.model_validate(value)
                try:
                    data = store.image_path(plan["draft_id"], image).read_bytes()
                except OSError:
                    raise GeneratedExportError("CASE_IMAGE_MISSING") from None
                if sha256(data).hexdigest() != image.sha256:
                    raise GeneratedExportError("CASE_IMAGE_MISSING")
                image_data.append(data)
            link = DraftLink.model_validate(plan["link"]) if plan["link"] else None
            fill_template(content, temporary, remove_thumbnail=True, images=image_data, link=link)
            validate_pptx(temporary)
            with ZipFile(temporary) as package:
                presentation = E.fromstring(package.read("ppt/presentation.xml"))
                root = E.fromstring(package.read(SLIDE_PART))
                if len(presentation.findall("p:sldIdLst/p:sldId", NS)) != 1:
                    raise GeneratedExportError("CASE_EXPORT_FAILED")
                shapes = {s.find("p:nvSpPr/p:cNvPr", NS).get("name"): s for s in root.findall("p:cSld/p:spTree/p:sp", NS)}
                actual_title = "".join(shapes["TITLE"].xpath(".//a:t/text()", namespaces=NS))
                if actual_title != content.export_title:
                    raise GeneratedExportError("CASE_EXPORT_FAILED")
                if len(root.findall(".//p:pic", NS)) != len(image_data):
                    raise GeneratedExportError("CASE_EXPORT_FAILED")
                paragraphs = shapes["BODY COPY"].findall("p:txBody/a:p", NS)
                for index, name in enumerate(("brief", "creative", "impact")):
                    actual = "".join(paragraphs[index * 2 + 1].xpath(".//a:t/text()", namespaces=NS))
                    if actual != (getattr(content, name) or ""):
                        raise GeneratedExportError("CASE_EXPORT_FAILED")
                hyperlink_relationships = [relationship for relationship in E.fromstring(package.read(
                    "ppt/slides/_rels/slide1.xml.rels")) if relationship.get("Type", "").endswith("/hyperlink")]
                clicks = root.findall(".//a:hlinkClick", NS)
                if link:
                    label = "".join(paragraphs[-1].xpath(".//a:t/text()", namespaces=NS))
                    if (len(paragraphs) != 7 or label != link.label or len(clicks) != 1
                            or len(hyperlink_relationships) != 1 or hyperlink_relationships[0].get("Target") != link.url
                            or clicks[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                            != hyperlink_relationships[0].get("Id")):
                        raise GeneratedExportError("CASE_EXPORT_FAILED")
                elif len(paragraphs) != 6 or clicks or hyperlink_relationships:
                    raise GeneratedExportError("CASE_EXPORT_FAILED")
            if template_hash() != plan["template_sha256"]:
                raise GeneratedExportError("CASE_TEMPLATE_CHANGED")
            temporary.replace(output)
        store.record_export(plan["draft_id"], summary)
        return summary
    except TemplateError:
        raise GeneratedExportError("CASE_TEMPLATE_INVALID") from None
    except (OSError, IngestionError, KeyError, E.XMLSyntaxError):
        raise GeneratedExportError("CASE_EXPORT_FAILED") from None
    finally:
        temporary.unlink(missing_ok=True)
