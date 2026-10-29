from pathlib import Path

from app.db.repository import init_db, save_cases
from app.ingestion.pptx_parser import parse_pptx, build_cases
from app.ingestion.errors import IngestionError
from app.ingestion.validation import validate_pptx
from app.models import FieldProvenance
from app.services.source_service import release_snapshot, snapshot_local_source
from app.services.web_enrichment import fetch_case_page
from app.services.enrichment_service import enrich_source
from app.services.ai_enrichment import infer_missing_metadata


def ingest_deck(
    pptx_path: str | Path,
    db_path: str | Path,
    source_deck: str | None = None,
    *,
    source_type: str = "LOCAL",
    original_source_url: str | None = None,
    source_file_id: str | None = None,
    source_version: str | None = None,
    source_id: str | None = None,
    progress=None,
    refresh_pages: bool = False,
) -> dict:
    # All acquired local bytes, including future authorized cloud downloads,
    # enter this same validation/snapshot/parse/commit boundary.
    if source_type not in {"LOCAL", "ONEDRIVE", "SHAREPOINT"}:
        raise IngestionError("UNSUPPORTED_SOURCE")
    if progress:
        progress("Validating presentation", 0, 1)
    validate_pptx(pptx_path)
    if progress:
        progress("Saving snapshot", 0, 1)
    source = snapshot_local_source(
        pptx_path, db_path, filename=source_deck or Path(pptx_path).name,
        source_type=source_type, original_source_url=original_source_url,
        source_file_id=source_file_id, source_version=source_version,
        source_id=source_id,
    )
    try:
        if progress:
            progress("Parsing slides", 0, 1)
        slides = parse_pptx(source.source_snapshot_reference)
        cases = build_cases(slides, source_deck=source.filename, source_id=source.source_id)
        if progress:
            progress("Saving cases", len(cases), len(cases))
        source.ingestion_status = "PARSED"
        init_db(db_path)
        save_cases(
            db_path,
            cases,
            slide_text_by_number={slide.slide_number: slide.text for slide in slides},
            source=source,
        )
    except IngestionError:
        release_snapshot(source, db_path, committed=False)
        raise
    except Exception as exc:
        release_snapshot(source, db_path, committed=False)
        raise IngestionError("PARSE_FAILED") from exc
    release_snapshot(source, db_path, committed=True)
    if progress:
        progress("Web metadata", 0, 1)
    enrichment = enrich_source(source.source_id, db_path, progress=progress,
                               refresh=refresh_pages, fetcher=fetch_case_page)
    by_id = {case.case_id: case for case in cases}
    for case_id, name, value, url in enrichment.pop("applied"):
        if case_id in by_id:
            setattr(by_id[case_id], name, value)
            by_id[case_id].field_provenance[name] = FieldProvenance("LINK_EXTRACTED", url)
    ai = infer_missing_metadata(source.source_id, db_path, progress=progress)
    return {
        "source_id": source.source_id,
        "source": source,
        "slide_count": len(slides),
        "case_slide_count": sum(1 for slide in slides if slide.is_case_study),
        "case_count": len(cases),
        "slides": slides,
        "cases": cases,
        "enrichment": enrichment,
        "ai": ai,
    }
