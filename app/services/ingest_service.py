from pathlib import Path
from app.ingestion.pptx_parser import parse_pptx, build_cases
from app.models import determine_case_status
from app.db.repository import init_db, save_cases
from app.services.web_enrichment import fetch_case_page



def ingest_deck(
    pptx_path: str | Path,
    db_path: str | Path,
    source_deck: str | None = None
) -> dict:
    slides = parse_pptx(pptx_path)

    source_deck = source_deck or Path(pptx_path).name
    cases = build_cases(slides, source_deck=source_deck)
    # Enrich only from approved Atomized Studios pages; external-page failures are non-fatal.
    for case in cases:
        for url in case.source_links:
            page = fetch_case_page(url)
            if page.get("text"):
                case.linked_page_text = page["text"]
            if page.get("client"):
                case.client = page["client"]
            if page.get("services"):
                case.services = page["services"]
            if case.linked_page_text and case.client and case.services:
                break
            
        # Recalculate status after webpage enrichment.
        case.review_status = determine_case_status(case)

    init_db(db_path)
    save_cases(
        db_path,
        cases,
        slide_text_by_number={slide.slide_number: slide.text for slide in slides},
    )
    return {
        "slide_count": len(slides),
        "case_slide_count": sum(1 for slide in slides if slide.is_case_study),
        "case_count": len(cases),
        "slides": slides,
        "cases": cases,
    }
