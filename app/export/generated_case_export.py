"""Freeze one draft and fill the approved single-slide template locally."""
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

from lxml import etree as E

from app import config
from app.generation.models import Content
from app.generation.template import fill_template, TemplateError, NS, SLIDE_PART
from app.ingestion.validation import validate_pptx
from app.ingestion.errors import IngestionError

ENGINE_VERSION = "generated-case-v1"
ERROR_MESSAGES = {
    "CASE_EXPORT_INCOMPLETE": "Add a title and at least one body section before generating the slide.",
    "CASE_EXPORT_BODY_TOO_LONG": "Shorten the combined Brief, Creative and Impact to 250 words or fewer.",
    "CASE_EXPORT_TITLE_TOO_LONG": "Shorten the title to 40 characters or fewer before generating the slide.",
    "CASE_TEMPLATE_INVALID": "The single-case template is missing or unsupported. Restore it in assets/templates.",
    "CASE_TEMPLATE_CHANGED": "The single-case template changed during generation. Generate the slide again.",
    "CASE_EXPORT_FAILED": "The slide could not be generated. Your saved draft is retained; please try again.",
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
    if len(content.title) > 40:
        raise GeneratedExportError("CASE_EXPORT_TITLE_TOO_LONG")
    plan = {"draft_id": draft.draft_id, "revision": draft.revision, "content": content.model_dump(),
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
    summary = {key: plan[key] for key in ("draft_id", "revision", "cache_key", "content_hash", "template_sha256", "engine_version")}
    summary.update(output_path=str(output), slide_count=1)
    try:
        if not output.is_file():
            content = Content(**plan["content"])
            fill_template(content, temporary, remove_thumbnail=True)
            validate_pptx(temporary)
            with ZipFile(temporary) as package:
                presentation = E.fromstring(package.read("ppt/presentation.xml"))
                root = E.fromstring(package.read(SLIDE_PART))
                if len(presentation.findall("p:sldIdLst/p:sldId", NS)) != 1:
                    raise GeneratedExportError("CASE_EXPORT_FAILED")
                shapes = {s.find("p:nvSpPr/p:cNvPr", NS).get("name"): s for s in root.findall("p:cSld/p:spTree/p:sp", NS)}
                paragraphs = shapes["BODY COPY"].findall("p:txBody/a:p", NS)
                for index, name in enumerate(("brief", "creative", "impact")):
                    actual = "".join(paragraphs[index * 2 + 1].xpath(".//a:t/text()", namespaces=NS))
                    if actual != (getattr(content, name) or ""):
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
