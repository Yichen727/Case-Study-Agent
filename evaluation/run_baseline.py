"""Offline measurement of the current Demo using an isolated temporary database.

Run from the repository root with: python -B -m evaluation.run_baseline PATH_TO_PPTX
Web enrichment is stubbed as unavailable; no company material is sent to a service.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlparse

from app.db.repository import init_db, list_cases, save_cases
from app.ingestion.pptx_parser import parse_pptx
from app.search.keyword_search import search_cases
from app.services.ingest_service import ingest_deck
from app.services.web_enrichment import ALLOWED_HOSTS


def latency(samples: list[float]) -> dict:
    ordered = sorted(samples)
    return {
        "runs": len(ordered),
        "p50_ms": round(ordered[math.ceil(len(ordered) * 0.50) - 1], 3),
        "p95_ms": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


def measure(operation, repetitions: int = 20) -> dict:
    samples = []
    for _ in range(repetitions):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1000)
    return latency(samples)


def baseline(source: Path, manifest: dict) -> dict:
    timings = {}
    fetched_urls = []

    def timed(name, operation):
        def run(*args, **kwargs):
            started = perf_counter()
            result = operation(*args, **kwargs)
            timings[name] = round((perf_counter() - started) * 1000, 3)
            return result
        return run

    def unavailable_page(url, **kwargs):
        fetched_urls.append(url)
        return {"text": None, "client": None, "services": None}

    with source.open("rb") as handle:
        source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    result = {
        "scope": "offline ingest with unavailable-page stub; isolated DB; no live HTTP/AI",
        "source": {"filename": source.name, "bytes": source.stat().st_size, "sha256": source_hash},
        "environment": {
            "platform": platform.platform(), "python": platform.python_version(),
            "executable": sys.executable, "sqlite": sqlite3.sqlite_version,
            "packages": {name: importlib.metadata.version(name) for name in
                         ("streamlit", "python-pptx", "pytest", "requests", "beautifulsoup4")},
        },
    }
    with TemporaryDirectory(prefix="case-study-baseline-") as temporary:
        db_path = Path(temporary) / "baseline.sqlite3"
        with (
            patch("app.services.ingest_service.parse_pptx", timed("parse_ms", parse_pptx)),
            patch("app.services.ingest_service.save_cases", timed("save_ms", save_cases)),
            patch("app.services.ingest_service.fetch_case_page", unavailable_page),
            patch("app.services.ingest_service.infer_missing_metadata", return_value={"status":"DISABLED_FOR_OFFLINE_EVALUATION"}),
        ):
            started = perf_counter()
            ingested = ingest_deck(source, db_path, source_deck=source.name)
            timings["offline_ingest_ms"] = round((perf_counter() - started) * 1000, 3)
        by_number = {slide.slide_number: slide for slide in ingested["slides"]}
        actual_case_slides = sorted(n for n, slide in by_number.items() if slide.is_case_study)
        expected_case_slides = manifest["expected_case_slides"]
        result["counts"] = {
            "slides": ingested["slide_count"], "cases": ingested["case_count"],
            "all_three_sections": sum(all((s.brief, s.creative, s.impact)) for s in by_number.values()),
            "untitled_cases": sum(not by_number[n].case_name for n in actual_case_slides),
            "cases_missing_client": sum(not c.client for c in ingested["cases"]),
            "cases_missing_services": sum(not c.services for c in ingested["cases"]),
        }
        result["identification"] = {
            "slide_count_matches": ingested["slide_count"] == manifest["expected_slide_count"],
            "case_slides_match": actual_case_slides == expected_case_slides,
            "missing_slides": sorted(set(expected_case_slides) - set(actual_case_slides)),
            "unexpected_slides": sorted(set(actual_case_slides) - set(expected_case_slides)),
        }
        checks = []
        for expected in manifest["annotations"]:
            slide = by_number[expected["slide"]]
            values = [slide.brief, slide.creative, slide.impact]
            checks.append({
                "slide": slide.slide_number, "expected_title": expected["title"],
                "actual_title": slide.case_name, "title_ok": slide.case_name == expected["title"],
                "sections_ok": all(values) if expected["sections"] == "all" else all(v is None for v in values),
                "links_ok": (
                    any(link["text"] == expected["link_label"] for link in slide.hyperlink_labels)
                    if "link_label" in expected else not slide.hyperlinks if expected.get("no_links") else True
                ),
            })
        result["annotation_checks"] = checks
        result["queries"] = []
        for query in manifest["queries"]:
            rows = search_cases(str(db_path), query["text"], limit=100)
            numbers = sorted(int(n) for row in rows for n in row["slide_numbers"].split(","))
            result["queries"].append({
                "text": query["text"], "returned_slides": numbers,
                "required_slides_present": set(query["must_include_slides"]).issubset(numbers),
                "latency": measure(lambda: search_cases(str(db_path), query["text"], limit=20)),
            })
        result["list_cases_latency"] = measure(lambda: list_cases(db_path))
        approved_urls = [url for url in fetched_urls if urlparse(url).scheme == "https"
                         and urlparse(url).hostname in ALLOWED_HOSTS]
        result["web"] = {
            "fetch_case_page_calls": len(fetched_urls), "unique_urls": len(set(fetched_urls)),
            "allowed_request_opportunities": len(approved_urls),
            "unique_allowed_urls": len(set(approved_urls)), "actual_http_requests": 0,
            "note": "All page fetches stubbed unavailable; live latency and successful-page early exits not measured.",
        }
        from streamlit.testing.v1 import AppTest

        with (
            patch("app.config.DB_PATH", db_path),
            patch("app.db.repository.list_cases", wraps=list_cases) as lists,
            patch("app.db.repository.init_db", wraps=init_db) as initializations,
        ):
            app = AppTest.from_file(str(Path("app/ui/streamlit_app.py").resolve()))
            started = perf_counter()
            app.run(timeout=30)
            initial_ms = round((perf_counter() - started) * 1000, 3)
            first_counts = {"list_cases_calls": lists.call_count, "init_db_calls": initializations.call_count}
            lists.reset_mock()
            initializations.reset_mock()
            rerun_timing = measure(lambda: app.run(timeout=30), repetitions=5)
            result["ui"] = {
                "scope": "Streamlit AppTest server execution; excludes browser rendering and file upload",
                "initial_run_ms": initial_ms, "initial_calls": first_counts,
                "rerun_latency": rerun_timing,
                "list_cases_calls_per_rerun": lists.call_count / 5,
                "init_db_calls_per_rerun": initializations.call_count / 5,
                "exceptions": [str(error.message) for error in app.exception],
            }
        result["timings"] = timings
        # Repository connections use context managers without explicit close();
        # collect released connections before Windows deletes the temporary DB.
        gc.collect()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation/baseline_latest.json"))
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("sample_manifest.json").read_text(encoding="utf-8"))
    report = baseline(args.source, manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Report:", args.output)
    print("Counts:", report["counts"])
    print("Timings:", report["timings"])
    print("Annotation failures:", [c["slide"] for c in report["annotation_checks"]
                                   if not all((c["title_ok"], c["sections_ok"], c["links_ok"]))])


if __name__ == "__main__":
    main()
