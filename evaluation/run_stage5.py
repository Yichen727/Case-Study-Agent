"""Evaluate the five company-deck prompts. --live explicitly enables OpenAI calls."""
import argparse
from contextlib import nullcontext, closing
import json
from pathlib import Path
from time import perf_counter
from tempfile import TemporaryDirectory
import sqlite3
from unittest.mock import patch

from app.config import AISettings, DB_PATH, get_ai_settings
from app.db.repository import connect, init_db
from app.search.embedding_service import build_index, index_status
from app.search.hybrid_search import search_cases


def usage(db):
    with connect(db) as conn:
        return {row["purpose"]:{"requests":row["requests"],"input_tokens":row["input_tokens"],"output_tokens":row["output_tokens"]}
                for row in conn.execute("""SELECT purpose,COUNT(*) AS requests,SUM(input_tokens) AS input_tokens,
                                          SUM(output_tokens) AS output_tokens FROM search_usage GROUP BY purpose""")}


def evaluate(db, live):
    init_db(db)
    prompts = json.loads(Path(__file__).with_name("stage5_prompts.json").read_text(encoding="utf-8"))
    report = {"scope":"live company-text embeddings and query interpretation" if live else "offline retrieval; existing query/vector caches may be reused; no new OpenAI calls",
              "prompts":[],"usage_before":usage(db)}
    started = perf_counter()
    report["index_build"] = build_index(db)
    report["index_build_ms"] = round((perf_counter()-started)*1000,3)
    if live and report["index_build"]["status"] != "COMPLETED":
        raise SystemExit("Semantic indexing unavailable: " + str(report["index_build"]["error_code"]))
    report["index_status"] = index_status(db)
    for sample in prompts:
        started = perf_counter()
        results = search_cases(db,sample["prompt"],100)
        elapsed = round((perf_counter()-started)*1000,3)
        by_slide = {int(number):row for row in results for number in row["slide_numbers"].split(",")}
        if live and (results.mode != "hybrid" or results.intent is None):
            raise SystemExit("Live search unavailable: " + "; ".join(results.notices))
        started = perf_counter()
        repeat = search_cases(db,sample["prompt"],100)
        repeated_ms = round((perf_counter()-started)*1000,3)
        positions = {int(number):position for position,row in enumerate(results,1) for number in row["slide_numbers"].split(",")}
        checks = {
            "primary_in_top10":all(positions.get(number,999)<=10 for number in sample["primary_slides"]),
            "expected_high":all(by_slide.get(number,{}).get("match_level")=="High Match" for number in sample.get("expected_high_slides",[])),
            "expected_partial":all(by_slide.get(number,{}).get("match_level")=="Partial Match" for number in sample.get("expected_partial_slides",[])),
            "no_forbidden_high":all(by_slide.get(number,{}).get("match_level")!="High Match" for number in sample["must_not_be_high_slides"]),
        }
        if not sample.get("expected_high_slides"):
            checks["no_high_without_both_requirements"] = not any(row["match_level"]=="High Match" for row in results)
        entry = {"id":sample["id"],"query":sample["prompt"],"mode":results.mode,"elapsed_ms":elapsed,
                 "repeat_ms":repeated_ms,"repeat_intent_cache_hit":repeat.intent_cache_hit,
                 "repeat_query_cache_hit":repeat.query_cache_hit,"intent":results.intent,"checks":checks,
                 "primary_positions":{str(n):positions.get(n) for n in sample["primary_slides"]},
                 "top_results":[{"slides":row["slide_numbers"],"title":row["title"],"level":row["match_level"],
                                 "reason":row["match_reason"],"conditions":row["condition_checks"],"evidence":row["evidence"]}
                                for row in results[:10]]}
        report["prompts"].append(entry)
        print(sample["id"],entry["primary_positions"],checks,"ms:",elapsed,"cached:",repeated_ms,flush=True)
    report["usage_after"] = usage(db)
    return report


def ui_measurement(db, prompt):
    from streamlit.testing.v1 import AppTest
    from app.services.task_service import get_task_manager
    settings = get_ai_settings()
    safe_settings = AISettings("",settings.model,settings.embedding_model)
    with (patch("app.config.DB_PATH",db),patch("app.config.get_ai_settings",return_value=safe_settings),
          patch("streamlit.file_uploader",return_value=[]),
          patch("app.search.embedding_service.embed_texts",side_effect=AssertionError("Unexpected UI embedding call")),
          patch("app.search.intent.OpenAI",side_effect=AssertionError("Unexpected UI interpretation call"))):
        app = AppTest.from_file(str(Path("app/ui/streamlit_app.py").resolve())).run(timeout=30)
        next(field for field in app.text_area if field.label=="Search cases").set_value(prompt)
        started = perf_counter()
        next(button for button in app.button if button.label=="Search").click().run()
        submit_ms = round((perf_counter()-started)*1000,3)
        times = []
        for _ in range(5):
            started = perf_counter()
            app.run()
            times.append(round((perf_counter()-started)*1000,3))
        exceptions = [item.message for item in app.exception]
        get_task_manager(db).shutdown()
    return {"scope":"AppTest server execution; browser drawing excluded; API constructors blocked",
            "cached_submit_ms":submit_ms,"rerun_ms":times,"exceptions":exceptions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--database",type=Path,default=DB_PATH)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--cold",action="store_true",help="Clone DB; clear query caches only in the disposable clone.")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="case-study-stage5-") as folder:
        db = args.database
        if args.cold:
            db = Path(folder) / "benchmark.sqlite3"
            with closing(sqlite3.connect(args.database)) as original, closing(sqlite3.connect(db)) as clone:
                original.backup(clone)
            with connect(db) as conn:
                conn.execute("DELETE FROM tasks")
                conn.execute("DELETE FROM query_intent_cache")
                conn.execute("DELETE FROM embedding_cache WHERE content_hash NOT IN (SELECT content_hash FROM search_documents)")
                conn.execute("DELETE FROM search_usage")
        with (nullcontext() if args.live else patch("app.config.get_ai_settings",return_value=AISettings())):
            report = evaluate(db,args.live)
            if args.live and args.cold:
                samples = json.loads(Path(__file__).with_name("stage5_prompts.json").read_text(encoding="utf-8"))
                report["ui"] = ui_measurement(db,samples[0]["prompt"])
        report["fresh_query_caches_in_isolated_clone"] = args.cold
    output = args.output or Path("evaluation/stage5_live.json" if args.live else "evaluation/stage5_keyword_baseline.json")
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Report:",output)


if __name__ == "__main__":
    main()
