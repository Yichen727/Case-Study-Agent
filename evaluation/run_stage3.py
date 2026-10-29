"""Offline stage-3 measurements. HTTP is simulated; source and current DB stay unchanged.

python -B -m evaluation.run_stage3 PATH_TO_PPTX
"""
import argparse
from shutil import copyfile
from zipfile import ZipFile
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter
from unittest.mock import patch

from streamlit.proto.WidgetStates_pb2 import WidgetState
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import ElementTree

from app.db.repository import count_cases, list_cases, list_sources
from app.config import AISettings
from app.search.keyword_search import search_cases
from app.services.ingest_service import ingest_deck
from app.services.task_service import get_task_manager
from app.services.web_enrichment import fetch_case_page
from evaluation.run_baseline import measure


class PageBody(BytesIO):
    def read1(self, size, decode_content=True):
        return self.read(size)


class OfflinePage:
    status_code = 200
    headers = {}

    def __enter__(self):
        self.raw = PageBody(b"<html><p>Offline fixture evidence</p><p>Client</p><p>Fixture client</p>"
                            b"<p>Services</p><p>Fixture services</p></html>")
        return self

    def __exit__(self, *args):
        self.raw.close()

    def raise_for_status(self):
        pass


def benchmark(source):
    with patch("app.config.get_ai_settings",return_value=AISettings()):
        return _benchmark(source)


def _benchmark(source):
    report = {"scope": "isolated DB; company PPTX; simulated HTTP; AppTest excludes browser painting/upload",
              "source": source.name, "source_bytes": source.stat().st_size}
    entered, release = Event(), Event()
    original_widget_states = ElementTree.get_widget_states

    def browser_tabs(tree):
        states = original_widget_states(tree)
        for block in tree.get("tab_container"):
            states.widgets.append(WidgetState(id=block.proto.tab_container.id,
                                              string_value=tree.session_state["active_tab"]))
        return states

    def held_enrichment(url, **kwargs):
        entered.set()
        if not release.wait(30):
            raise TimeoutError("Measurement did not release its simulated web task")
        return fetch_case_page(url, **kwargs)

    with TemporaryDirectory(prefix="case-study-stage3-") as temporary:
        db = Path(temporary) / "cases.sqlite3"
        with (patch("app.services.web_enrichment.requests.get", side_effect=lambda *args, **kwargs: OfflinePage()) as http,
              patch("app.services.ingest_service.infer_missing_metadata",return_value={"status":"DISABLED_FOR_OFFLINE_EVALUATION"})):
            started = perf_counter()
            initial = ingest_deck(source, db)
            report["first_import"] = {"elapsed_ms": round((perf_counter() - started) * 1000, 3),
                                      "case_count": initial["case_count"], "enrichment": initial["enrichment"]}
            repeated = ingest_deck(source,db)
            report["identical_reupload"] = {"reused":repeated["reused"],"stored_cases":count_cases(db)}
            version = Path(temporary) / "new-file-version.pptx"
            copyfile(source,version)
            with ZipFile(version,"a") as deck:
                deck.comment = b"Byte-distinct version for background import measurement."
            del initial
            manager = get_task_manager(db)
            try:
                with (patch("app.config.DB_PATH", db), patch("streamlit.file_uploader", return_value=[]),
                      patch.object(ElementTree, "get_widget_states", browser_tabs),
                      patch("app.db.repository.list_cases", wraps=list_cases) as reads):
                    app = AppTest.from_file(str(Path("app/ui/streamlit_app.py").resolve())).run(timeout=30)
                    assert not app.exception
                    reads.reset_mock()
                    report["idle_search_tab_rerun"] = measure(lambda: app.run(timeout=30), 5)
                    report["idle_search_tab_list_cases_calls"] = reads.call_count
                    http.reset_mock()
                    with patch("app.services.ingest_service.fetch_case_page", held_enrichment):
                        started = perf_counter()
                        task_id = manager.submit_import(version, source.name)
                        report["queue_submit_including_disk_spool_ms"] = round((perf_counter() - started) * 1000, 3)
                        assert entered.wait(15)
                        report["cases_visible_before_web_finishes"] = count_cases(db)
                        report["search_while_task_running"] = measure(lambda: search_cases(db, "sustainability"), 20)
                        next(field for field in app.text_area if field.label == "Search cases").set_value("sustainability")
                        next(button for button in app.button if button.label == "Search").click().run()
                        report["rendered_search_results"] = len(app.session_state["search_results"])
                        report["ui_rerun_while_task_running"] = measure(lambda: app.run(timeout=30), 5)
                        assert manager.get_task(task_id)["status"] == "RUNNING"
                        app.session_state["active_tab"] = "All cases"
                        reads.reset_mock()
                        app.run()
                        assert not app.exception
                        report["all_cases_page_rows"] = len(app.dataframe[0].value)
                        report["all_cases_list_arguments"] = list(reads.call_args.args[1:])
                        release.set()
                        task = manager.wait(task_id)
                    assert task["status"] == "SUCCEEDED"
                    report["second_import"] = json.loads(task["result_json"])
                    report["second_import"]["source_id"] = "independent source ID for byte-distinct version (omitted)"
                    report["second_import_actual_http_calls"] = http.call_count
                    report["stored_sources"] = len(list_sources(db))
                    report["stored_cases"] = count_cases(db)
                    report["snapshot_files"] = len(list((Path(temporary) / "uploads").glob("*.pptx")))
                    report["successful_task_input_files"] = len(list((Path(temporary) / "incoming").glob("*.pptx")))
                    report["ui_exceptions"] = [error.message for error in app.exception]
            finally:
                release.set()
                manager.shutdown()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation/stage3_measurements.json"))
    args = parser.parse_args()
    report = benchmark(args.source.resolve())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
