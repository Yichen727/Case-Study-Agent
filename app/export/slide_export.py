"""Resolve ordered cases once, then export their immutable original snapshots."""
import hashlib
import json
from pathlib import Path

from app.db.repository import connect
from app.export.openxml_export import export_slides, SlideReference

EXPORT_ENGINE_VERSION = "openxml-v2"
ERROR_MESSAGES = {
    "EXPORT_SELECTION_INVALID":"Select at least one case, without selecting the same case twice.",
    "EXPORT_CASE_MISSING":"A selected case is no longer available. Update the selection and confirm again.",
    "EXPORT_SOURCE_MISSING":"An original presentation snapshot is missing. Re-import the original deck before exporting.",
    "EXPORT_SOURCE_CHANGED":"An original snapshot has changed. Re-import the deck before exporting.",
    "EXPORT_SLIDE_MISSING":"An original slide could not be found. Re-import its deck before exporting.",
    "EXPORT_SIZE_MISMATCH":"These decks have different slide sizes. Select decks with the same slide size.",
    "EXPORT_LINK_TARGET_MISSING":"An internal link points to another slide. Include its destination slide and confirm again.",
    "EXPORT_EMBEDDED_FONTS":"Combining these decks with embedded fonts is not supported yet. Export them separately.",
    "EXPORT_VERSION_CHANGED":"The export engine was updated. Generate this presentation again.",
    "EXPORT_FAILED":"The presentation could not be exported. Please try again.",
}


class CaseExportError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(export_error_message(code))


def export_error_message(code):
    return ERROR_MESSAGES.get(code,ERROR_MESSAGES["EXPORT_FAILED"])


def prepare_export(db_path, case_ids):
    ids = list(case_ids)
    if not ids or len(set(ids))!=len(ids):
        raise CaseExportError("EXPORT_SELECTION_INVALID")
    with connect(db_path) as conn:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(f"""SELECT c.case_id,c.title,c.source_deck,c.original_slide_id,c.review_status,
                               src.source_id,src.source_snapshot_reference,src.content_sha256,src.requires_reimport,
                               GROUP_CONCAT(s.slide_number,',') AS slide_numbers,
                               EXISTS(SELECT 1 FROM field_metadata m WHERE m.case_id=c.case_id
                                      AND m.source_kind='AI_INFERRED' AND m.verification_status!='VERIFIED') AS unverified_ai
                               FROM cases c JOIN sources src ON src.source_id=c.source_id
                               LEFT JOIN slides s ON s.case_id=c.case_id
                               WHERE c.case_id IN ({marks}) GROUP BY c.case_id""",ids).fetchall()
    cases = {row["case_id"]:dict(row) for row in rows}
    if len(cases)!=len(ids):
        raise CaseExportError("EXPORT_CASE_MISSING")
    slides,labels = [],[]
    folder = Path(db_path).resolve().parent / "uploads"
    for case_id in ids:
        case = cases[case_id]
        snapshot = case["source_snapshot_reference"]
        if case["requires_reimport"] or not snapshot or not case["content_sha256"] or case["original_slide_id"] is None:
            raise CaseExportError("EXPORT_SOURCE_MISSING")
        path = Path(snapshot).resolve()
        if path.parent!=folder or not path.is_file():
            raise CaseExportError("EXPORT_SOURCE_MISSING")
        slides.append({"path":str(path),"slide_id":case["original_slide_id"],"sha256":case["content_sha256"]})
        labels.append({key:case[key] for key in ("title","source_deck","slide_numbers","review_status","unverified_ai")})
    identity = {"case_ids":ids,"slides":[(slide["slide_id"],slide["sha256"]) for slide in slides],
                "engine_version":EXPORT_ENGINE_VERSION}
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    return {**identity,"slides":slides,"cases":labels,"cache_key":key}


def export_original_slides(plan, output_path, *, progress=None):
    if plan["engine_version"]!=EXPORT_ENGINE_VERSION:
        raise CaseExportError("EXPORT_VERSION_CHANGED")
    snapshots = {slide["path"]:slide["sha256"] for slide in plan["slides"]}
    for index,(path,expected) in enumerate(snapshots.items()):
        if progress:
            progress("Checking original presentation",index,len(snapshots))
        try:
            with Path(path).open("rb") as source:
                digest = hashlib.sha256()
                while chunk := source.read(1024*1024):
                    digest.update(chunk)
                actual = digest.hexdigest()
        except OSError:
            raise CaseExportError("EXPORT_SOURCE_MISSING") from None
        if actual!=expected:
            raise CaseExportError("EXPORT_SOURCE_CHANGED")
    if progress:
        progress("Exporting original slides",0,len(plan["slides"]))
    refs = [SlideReference(Path(slide["path"]),slide["slide_id"]) for slide in plan["slides"]]
    return export_slides(refs,output_path)
