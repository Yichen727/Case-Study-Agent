"""Offline browser check of production selection UI using installed Edge and Playwright."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import patch

from app.config import AISettings
from app.services.ingest_service import ingest_deck


def evaluate(output, *, include_export=False):
    from playwright.sync_api import sync_playwright, expect

    root = Path(__file__).resolve().parents[1]
    report = {"scope":"Offline synthetic PPTX; real production UI and sortable component; headless local Edge",
              "checks":{},"browser_errors":[]}
    with TemporaryDirectory(prefix="stage6-",dir=root / "data") as folder:
        temporary = Path(folder)
        database = temporary / "cases.sqlite3"
        source_cases = {}
        with patch("app.config.get_ai_settings",return_value=AISettings()),patch("app.services.ingest_service.fetch_case_page",return_value={}):
            for name in ("source_a.pptx","source_b.pptx"):
                imported = ingest_deck(root / "evaluation/fixtures" / name,database,source_deck="same-name.pptx")
                source_cases.update({case.case_id:(name,case.slide_numbers[0]) for case in imported["cases"]})
        events = temporary / "search-events.txt"
        wrapper = temporary / "browser_app.py"
        wrapper.write_text(f'''import sys, runpy
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,{str(root)!r})
from app.config import AISettings
from app.search.keyword_search import search_cases
def observed_search(*args,**kwargs):
    with Path({str(events)!r}).open("a") as log: log.write("search\\n")
    return search_cases(*args,**kwargs)
with patch("app.config.DB_PATH",Path({str(database)!r})), patch("app.config.get_ai_settings",return_value=AISettings()), patch("app.search.keyword_search.search_cases",side_effect=observed_search), patch("app.search.intent.OpenAI",side_effect=AssertionError("Unexpected OpenAI call")), patch("app.search.embedding_service.OpenAI",side_effect=AssertionError("Unexpected embedding call")):
    runpy.run_path({str(root / "app/ui/streamlit_app.py")!r},run_name="__main__")
''',encoding="utf-8")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1",0))
            port = probe.getsockname()[1]
        env = dict(os.environ,PYTHONDONTWRITEBYTECODE="1",PYTHONPATH=str(root))
        env.pop("OPENAI_API_KEY",None)
        with (temporary / "server.log").open("w",encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable,"-B","-m","streamlit","run",str(wrapper),
                                       "--server.headless=true","--server.address=127.0.0.1",f"--server.port={port}",
                                       "--server.fileWatcherType=none","--browser.gatherUsageStats=false"],
                                      cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,
                                      creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            try:
                for _ in range(100):
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/_stcore/health",timeout=1) as health:
                            if health.status==200:
                                break
                    except URLError:
                        sleep(0.1)
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="msedge",headless=True)
                    try:
                        page = browser.new_page(viewport={"width":1440,"height":1100})
                        page.on("pageerror",lambda error:report["browser_errors"].append(str(error)))
                        page.goto(f"http://127.0.0.1:{port}")
                        page.get_by_role("textbox",name="Search cases",exact=True).fill("synthetic")
                        page.get_by_role("button",name="Search",exact=True).click()
                        page.get_by_text("Partial Match (3)",exact=True).click()
                        choices = page.get_by_role("checkbox",name="Select case",exact=True)
                        expect(choices).to_have_count(3)
                        started = perf_counter()
                        page.get_by_text("Select case",exact=True).nth(0).click()
                        expect(page.get_by_role("button",name="Selected cases (1)",exact=True)).to_be_visible()
                        report["first_checkbox_browser_ms"] = round((perf_counter()-started)*1000,3)
                        page.get_by_text("Select case",exact=True).nth(1).click()
                        expect(page.get_by_role("button",name="Selected cases (2)",exact=True)).to_be_visible()
                        page.get_by_text("Select case",exact=True).nth(2).click()
                        page.get_by_role("button",name="Selected cases (3)",exact=True).click()
                        cards = page.locator(".selected-case-card")
                        items = page.locator(".selected-case-label")
                        expect(items).to_have_count(3)
                        initial = items.all_text_contents()
                        report["checks"]["independent_same_title_and_slide_versions"] = len(set(initial))==3
                        page.get_by_role("button",name="Confirm order",exact=True).click()
                        expect(page.get_by_text("Order confirmed for 3 slides.",exact=True)).to_be_visible()
                        started = perf_counter()
                        cards.first.hover()
                        start,target = cards.first.bounding_box(),cards.nth(2).bounding_box()
                        page.mouse.move(start["x"]+start["width"]/2,start["y"]+start["height"]/2)
                        page.mouse.down()
                        page.mouse.move(start["x"]+start["width"]/2,start["y"]+start["height"]/2+12,steps=5)
                        page.mouse.move(target["x"]+target["width"]/2,target["y"]+target["height"]/2,steps=20)
                        page.mouse.up()
                        expect(items.first).to_have_text(initial[1])
                        expect(page.get_by_text("Order confirmed for 3 slides.",exact=True)).to_have_count(0)
                        report["drag_browser_ms"] = round((perf_counter()-started)*1000,3)
                        reordered = items.all_text_contents()
                        report["checks"]["real_drag_order"] = reordered==[initial[1],initial[2],initial[0]]
                        report["checks"]["drag_invalidates_confirmation"] = True
                        report["checks"]["selection_actions_do_not_search"] = len(events.read_text().splitlines())==1
                        page.get_by_role("button",name="Confirm order",exact=True).click()
                        expect(page.get_by_text("Order confirmed for 3 slides.",exact=True)).to_be_visible()
                        page.screenshot(path=str(output.with_suffix(".png")),full_page=True)
                        page.get_by_role("tab",name="Search results",exact=True).click()
                        page.get_by_role("textbox",name="Search cases",exact=True).fill("unmatchedzzzz")
                        page.get_by_role("button",name="Search",exact=True).click()
                        expect(page.get_by_text("No matching cases. Try a broader keyword.",exact=True)).to_be_visible()
                        page.get_by_role("button",name="Selected cases (3)",exact=True).click()
                        expect(items.first).to_have_text(reordered[0])
                        expect(page.get_by_text("Order confirmed for 3 slides.",exact=True)).to_be_visible()
                        report["checks"]["selection_order_and_confirmation_survive_new_search"] = True
                        before_remove_ids = cards.evaluate_all("rows => rows.map(row => row.dataset.caseId)")
                        page.get_by_role("button",name="Remove case: "+reordered[0].replace("\n"," · "),exact=True).click()
                        expect(items).to_have_count(2)
                        report["checks"]["cross_removes_exact_case"] = cards.evaluate_all("rows => rows.map(row => row.dataset.caseId)")==before_remove_ids[1:]
                        expect(page.get_by_text("Order confirmed for 3 slides.",exact=True)).to_have_count(0)
                        report["checks"]["remove_invalidates_confirmation"] = True
                        export_ids = cards.evaluate_all("rows => rows.map(row => row.dataset.caseId)")
                        page.get_by_role("button",name="Confirm order",exact=True).click()
                        expect(page.get_by_text("Order confirmed for 2 slides.",exact=True)).to_be_visible()
                        expect(page.get_by_role("tab",name="Export",exact=True)).to_have_count(0)
                        expect(page.get_by_role("button",name="Generate PPTX",exact=True)).to_be_enabled()
                        expect(page.get_by_text("Slide order",exact=True)).to_have_count(0)
                        report["checks"]["ordering_and_export_share_one_tab"] = True
                        report["checks"]["confirmed_order_available_for_phase7"] = True
                        if include_export:
                            from lxml import etree as ET
                            from pptx import Presentation
                            from app.db.repository import connect
                            from evaluation.export_poc import verify_package
                            started = perf_counter()
                            page.get_by_role("button",name="Generate PPTX",exact=True).click()
                            download = page.get_by_role("button",name="Download PPTX",exact=True)
                            expect(download).to_be_visible(timeout=60000)
                            report["generate_browser_ms"] = round((perf_counter()-started)*1000,3)
                            folder = output.parent / "artifacts/stage7"
                            folder.mkdir(parents=True,exist_ok=True)
                            downloaded = folder / "browser-selected.pptx"
                            with page.expect_download() as received:
                                download.click()
                            received.value.save_as(str(downloaded))
                            verify_package(downloaded)
                            slides = Presentation(downloaded).slides
                            assert len(slides)==len(export_ids)
                            for actual,case_id in zip(slides,export_ids):
                                filename,number = source_cases[case_id]
                                expected = Presentation(root / "evaluation/fixtures" / filename).slides[number-1]
                                assert ET.tostring(actual._element)==ET.tostring(expected._element)
                            report["checks"]["download_preserves_original_slides_and_order"] = True
                            page.get_by_role("button",name="Generate PPTX",exact=True).click()
                            expect(download).to_be_visible()
                            with connect(database) as conn:
                                tasks = conn.execute("SELECT * FROM tasks WHERE kind='EXPORT'").fetchall()
                            report["checks"]["repeated_generate_reuses_one_task"] = len(tasks)==1
                            report["export_output"] = str(downloaded)
                            report["export_bytes"] = downloaded.stat().st_size
                        page.get_by_role("tab",name="Search results",exact=True).click()
                        page.get_by_role("textbox",name="Search cases",exact=True).fill("synthetic")
                        page.get_by_role("button",name="Search",exact=True).click()
                        details = page.get_by_text("Partial Match (3)",exact=True)
                        expect(details).to_be_visible()
                        if not choices.first.is_visible():
                            details.click()
                        expect(choices).to_have_count(3)
                        expect(page.locator('input[type="checkbox"]:checked')).to_have_count(2)
                        report["checks"]["cross_updates_search_checkboxes"] = True
                        page.get_by_role("button",name="Selected cases (2)",exact=True).click()
                        expect(items).to_have_count(2)
                        cards.first.get_by_role("button").click()
                        expect(items).to_have_count(1)
                        cards.first.get_by_role("button").click()
                        expect(page.get_by_text("Choose cases using the checkboxes in Search results.",exact=True)).to_be_visible()
                        expect(page.get_by_role("button",name="Confirm order",exact=True)).to_have_count(0)
                        report["checks"]["consecutive_crosses_can_remove_last_case"] = True
                        page.get_by_role("tab",name="Search results",exact=True).click()
                        if not choices.first.is_visible():
                            page.get_by_text("Partial Match (3)",exact=True).click()
                        expect(choices).to_have_count(3)
                        expect(page.locator('input[type="checkbox"]:checked')).to_have_count(0)
                        report["checks"]["empty_selection_clears_all_checkboxes"] = True
                        if include_export:
                            page.get_by_role("button",name="Clear finished tasks",exact=True).click()
                            page.get_by_role("tab",name="Selected cases",exact=True).click()
                            expect(page.get_by_role("button",name="Generate PPTX",exact=True)).to_be_disabled()
                            expect(page.get_by_role("button",name="Download PPTX",exact=True)).to_have_count(0)
                            report["checks"]["clear_finished_removes_generated_files"] = not list((temporary / "exports").glob("*.pptx"))
                        report["search_requests"] = len(events.read_text().splitlines())
                        report["checks"]["only_explicit_searches_ran"] = report["search_requests"]==3
                        report["checks"]["no_streamlit_exceptions"] = page.get_by_test_id("stException").count()==0
                        report["checks"]["no_browser_errors"] = not report["browser_errors"]
                        report["initial_labels"],report["reordered_labels"] = initial,reordered
                    except Exception:
                        print("Frames:",page.locator("iframe").evaluate_all("frames => frames.map(frame => ({title:frame.title,src:frame.src}))"),flush=True)
                        print("Streamlit exceptions:",page.get_by_test_id("stException").all_text_contents(),flush=True)
                        page.screenshot(path=str(output.with_name("stage6_failure.png")),full_page=True)
                        raise
                    finally:
                        browser.close()
            finally:
                server.terminate()
                server.wait(timeout=10)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=Path("evaluation/stage6_browser.json"))
    parser.add_argument("--export",action="store_true",dest="include_export",help="Also verify phase-7 generation, download and cleanup.")
    args = parser.parse_args()
    report = evaluate(args.output.resolve(),include_export=args.include_export)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not all(report["checks"].values()):
        raise SystemExit("Stage 6 browser verification failed.")


if __name__ == "__main__":
    main()
