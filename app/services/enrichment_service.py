"""Supplement committed cases; slow HTTP never holds a database transaction."""
import json
from time import monotonic

from app.db.repository import apply_enrichment, list_cases
from app.services.web_enrichment import fetch_case_page, normalize_url

ENRICHMENT_BUDGET_SECONDS = 20


def enrich_source(source_id, db_path, *, progress=None, refresh=False, fetcher=None):
    fetcher = fetcher or fetch_case_page
    cases = list_cases(db_path, source_id=source_id)
    urls = {normalize_url(url) or url for case in cases for url in json.loads(case["source_links_json"])}
    pages = {}
    updates = []
    metrics = {"cache_hits": 0, "http_requests": 0, "skipped_budget": 0}
    deadline = monotonic() + ENRICHMENT_BUDGET_SECONDS
    for case in cases:
        if all(case[field] for field in ("client", "services", "linked_page_text")):
            continue
        labels = json.loads(case["source_link_labels_json"])
        ordered = [link["url"] for link in labels if link.get("link_type") == "READ_MORE"]
        ordered += json.loads(case["source_links_json"])
        for url in dict.fromkeys(ordered):
            normalized = normalize_url(url) or url
            if normalized not in pages:
                pages[normalized] = fetcher(normalized, cache_db=db_path, refresh=refresh,
                                            deadline=deadline, metrics=metrics)
                if progress:
                    progress("Web metadata", len(pages), len(urls))
            page = pages[normalized]
            for field, key in (("client", "client"), ("services", "services"), ("linked_page_text", "text")):
                if case[field] is None and page.get(key):
                    case[field] = page[key]
                    updates.append((case["case_id"], field, page[key], url))
            if all(case[field] for field in ("client", "services", "linked_page_text")):
                break
    applied = apply_enrichment(db_path, updates)
    return {**metrics, "unique_urls": len(pages), "unavailable_pages": sum(not page.get("text") for page in pages.values()),
            "updated_fields": len(applied), "applied": applied}
