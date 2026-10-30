"""Verify short AI-content query variants on a disposable copy of the company library."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter

from app.config import DB_PATH, get_ai_settings
from app.db.repository import connect
from app.search.hybrid_search import search_cases
from app.search.intent import normalize_query, PARSER_VERSION

VARIANTS = [
    "Show me the cases about AI generated content!",
    "Show me the case about AI generated content!",
    "Show me cases about AI-Generated content",
    "Find case studies about AI generated content.",
    "AI generated content",
]
CONTROLS = [
    "Show me cases about AI-made content",
    "Show me cases about AI generated content. Must use AI dubbing.",
    "Show me cases about AI generated content. Must have at least 10 million views.",
]


def evaluate(output):
    if not get_ai_settings().api_key:
        raise SystemExit("Configure the local OpenAI key before running the live query check.")
    report={"scope":"Live query interpretation and query embeddings only; isolated company DB copy, no case reembedding or PPTX upload",
            "parser_version":PARSER_VERSION,"queries":[],"checks":{}}
    with TemporaryDirectory(prefix="search-stability-") as folder:
        db=Path(folder) / "library.sqlite3"
        with closing(sqlite3.connect(DB_PATH.resolve().as_uri()+"?mode=ro",uri=True)) as source, closing(sqlite3.connect(db)) as target:
            source.backup(target)
        with connect(db) as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM query_intent_cache")
            conn.execute("DELETE FROM search_usage")
            conn.execute("DELETE FROM embedding_cache WHERE content_hash NOT IN (SELECT content_hash FROM search_documents)")
        baseline=None
        for index,query in enumerate([*VARIANTS,*CONTROLS]):
            started=perf_counter()
            results=search_cases(db,query,100)
            if results.intent is None or results.mode!="hybrid":
                raise AssertionError("Live search unavailable: "+"; ".join(results.notices))
            target=[case for case in results if case["title"]=="GEN AI BRAND FILM"]
            classes=[(case["case_id"],case["match_level"]) for case in results]
            if baseline is None:
                baseline=classes
            equivalent=index<len(VARIANTS)
            checks={"both_original_versions_present":len(target)==2,
                    "expected_classification":all(case["match_level"]=="High Match" for case in target) if index<=len(VARIANTS)
                        else all(case["match_level"]!="High Match" for case in target)}
            if equivalent:
                checks["equivalent_variants_have_identical_order_and_classes"]=classes==baseline
            entry={"query":query,"normalized":normalize_query(query),"summary":results.intent["summary"],
                   "intent":results.intent,"elapsed_ms":round((perf_counter()-started)*1000,2),
                   "intent_cache_hit":results.intent_cache_hit,"query_cache_hit":results.query_cache_hit,"checks":checks,
                   "target_results":[{"title":case["title"],"slide":case["slide_numbers"],"level":case["match_level"],
                                      "checks":case["condition_checks"],"reason":case["match_reason"]} for case in target]}
            report["queries"].append(entry)
            print(json.dumps({"query":query,"summary":entry["summary"],"ms":entry["elapsed_ms"],"checks":checks},ensure_ascii=False),flush=True)
        with connect(db) as conn:
            report["usage"]=[dict(row) for row in conn.execute("SELECT purpose,COUNT(*) AS requests,SUM(input_tokens) AS input_tokens,SUM(output_tokens) AS output_tokens FROM search_usage GROUP BY purpose")]
        report["checks"]={"all_query_checks_pass":all(all(entry["checks"].values()) for entry in report["queries"]),
                          "case_plural_variants_share_both_caches":all(entry["intent_cache_hit"] and entry["query_cache_hit"] for entry in report["queries"][1:len(VARIANTS)]),
                          "only_four_intent_requests":sum(item["requests"] for item in report["usage"] if item["purpose"]=="INTENT")==4}
    output=Path(output)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    assert all(report["checks"].values()),report["checks"]
    print("Report:",output)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true",help="Explicitly allow up to four intent and four query-vector requests.")
    parser.add_argument("--output",type=Path,default=Path("evaluation/search_stability_live.json"))
    args=parser.parse_args()
    if not args.live:
        parser.error("Use --live to enable the small query-only API validation.")
    evaluate(args.output)
