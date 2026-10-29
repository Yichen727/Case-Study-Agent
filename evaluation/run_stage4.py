"""Synthetic stage-4 checks. Default: real SDK + simulated HTTP, no external calls.

python -B -m evaluation.run_stage4
python -B -m evaluation.run_stage4 --live  # configured key; six synthetic cases
"""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import httpx
from openai import OpenAI as RealOpenAI
from pptx import Presentation
from pptx.util import Inches

from app.config import AISettings, get_ai_settings
from app.db.repository import get_case_text, get_field_metadata, list_cases
from app.models import case_display_status
from app.services import ai_enrichment as ai
from app.services.ingest_service import ingest_deck


def evaluate(live=False):
    samples = json.loads(Path(__file__).with_name("stage4_samples.json").read_text(encoding="utf-8"))
    settings = get_ai_settings()
    if live and not settings.api_key:
        raise SystemExit("Set OPENAI_API_KEY in the local .env file before using --live.")
    wire_requests = []
    evidence_checks = []
    supported = ai._supported

    def inspect_evidence(field, text, name):
        accepted = supported(field,text,name)
        evidence_checks.append({"field":name,"proposed_value":field.value,"quote":field.evidence,
                                "accepted":accepted.value is not None})
        return accepted

    def transport(request):
        data = json.loads(request.content)
        wire_requests.append(data)
        assert data["store"] is False and data["text"]["format"]["strict"] is True
        items = []
        for payload in json.loads(data["input"]):
            sample = next(sample for sample in samples if sample["title"] in payload["slide_text"])
            items.append({"item_id":payload["item_id"],
                          "client":{"value":sample["client"],"evidence":sample["brief"] if sample["client"] else None},
                          "services":{"value":sample["services"],"evidence":sample["creative"] if sample["services"] else None}})
        body = {"id":"resp_offline","object":"response","created_at":0,"status":"completed","model":settings.model,
                "output":[{"id":"msg_offline","type":"message","role":"assistant","status":"completed",
                           "content":[{"type":"output_text","text":json.dumps({"cases":items}),"annotations":[]}]}],
                "usage":{"input_tokens":0,"output_tokens":0,"total_tokens":0,
                         "input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":0}}}
        return httpx.Response(200,json=body)

    with TemporaryDirectory(prefix="case-study-stage4-") as folder, ExitStack() as stack:
        folder = Path(folder)
        path,db = folder / "synthetic.pptx",folder / "cases.sqlite3"
        deck = Presentation()
        for sample in samples:
            slide = deck.slides.add_slide(deck.slide_layouts[6])
            for top,text in ((0,"CASE STUDY SYNTHETIC"),(1,sample["title"]),
                             (2,f"BRIEF\n{sample['brief']}\nCREATIVE\n{sample['creative']}\nIMPACT\nOutcome not supplied.")):
                shape = slide.shapes.add_textbox(Inches(1),Inches(top),Inches(8),Inches(2))
                shape.text = text
        deck.save(path)
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        stack.enter_context(patch("app.services.ingest_service.infer_missing_metadata",return_value={"status":"DEFERRED_FOR_EVALUATION"}))
        stack.enter_context(patch.object(ai,"_supported",side_effect=inspect_evidence))
        if not live:
            stack.enter_context(patch.object(ai,"get_ai_settings",return_value=AISettings("offline-test-key",settings.model)))

            def sdk_client(**kwargs):
                return RealOpenAI(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(transport)))

            stack.enter_context(patch.object(ai,"OpenAI",side_effect=sdk_client))
        initial = ingest_deck(path,db)
        originals = {case["case_id"]:get_case_text(db,case["case_id"]) for case in list_cases(db)}
        started = perf_counter()
        first = ai.infer_missing_metadata(initial["source_id"],db)
        elapsed = round((perf_counter()-started)*1000,3)
        checks = []
        rows = {row["title"]:row for row in list_cases(db)}
        for sample in samples:
            row = rows[sample["title"]]
            services = (row["services"] or "").casefold()
            services_ok = (row["services"] is None if sample["services"] is None else
                           all(any(term in services for term in group) for group in sample["service_terms"]))
            client_ok = (row["client"] or "").casefold() == (sample["client"] or "").casefold()
            checks.append({"title":sample["title"],"client":row["client"],"services":row["services"],
                           "client_ok":client_ok,"services_ok":services_ok,
                           "unverified_ai_fields":row["unverified_ai_fields"]})
        second_source = ingest_deck(path,db)
        second = ai.infer_missing_metadata(second_source["source_id"],db)
        selected = rows[samples[0]["title"]]
        metadata = {row["field_name"]:row for row in get_field_metadata(db,selected["case_id"])}
        return {
            "scope":"live OpenAI on six synthetic slides" if live else "real OpenAI SDK with simulated HTTP; no external requests",
            "live_quality_validation":live,"model":settings.model,"prompt_version":ai.PROMPT_VERSION,
            "sample_count":len(samples),"elapsed_ms":elapsed,"first_run":first,"second_import_run":second,
            "simulated_sdk_http_requests":len(wire_requests),"checks":checks,
            "field_evidence_checks":evidence_checks,
            "client_checks_passed":sum(row["client_ok"] for row in checks),
            "services_checks_passed":sum(row["services_ok"] for row in checks),
            "original_text_unchanged":all(get_case_text(db,key)==value for key,value in originals.items()),
            "source_bytes_unchanged":hashlib.sha256(path.read_bytes()).hexdigest()==original_hash,
            "automatically_confirmed":case_display_status(selected)=="Confirmed" and selected["review_status"]=="CONFIRMED",
            "ai_origin_retained":metadata["client"]["source_kind"]=="AI_INFERRED" and metadata["client"]["verification_status"]=="UNVERIFIED",
            "cost_note":"Use recorded input/output tokens and account billing. Offline usage is synthetic zero; model accuracy remains unmeasured offline.",
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    report = evaluate(args.live)
    output = args.output or Path("evaluation/stage4_live.json" if args.live else "evaluation/stage4_offline.json")
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
