"""Offline worker exports from a company DB clone and synthetic multi-source cases."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch
from zipfile import ZipFile

from lxml import etree as ET
from pptx import Presentation

from app.config import AISettings, DB_PATH
from app.db.repository import connect, init_db
from app.export.slide_export import prepare_export
from app.services.ingest_service import ingest_deck
from app.services.task_service import TaskManager
from evaluation.export_poc import source_slides, verify_package


def verify_export(output, plan):
    verify_package(output)
    targets = source_slides(output)
    assert len(Presentation(output).slides)==len(plan["slides"])
    original_parts = {}
    expected_assets = set()
    for path in dict.fromkeys(slide["path"] for slide in plan["slides"]):
        original_parts[path] = dict(source_slides(path))
        with ZipFile(path) as source:
            for name in source.namelist():
                if name.startswith(("ppt/media/","ppt/charts/","ppt/embeddings/")) and not name.endswith(".rels"):
                    expected_assets.add(hashlib.sha256(source.read(name)).hexdigest())
    assets = 0
    with ZipFile(output) as exported:
        for index,slide in enumerate(plan["slides"]):
            part = original_parts[slide["path"]][slide["slide_id"]]
            destination = targets[index][1]
            with ZipFile(slide["path"]) as source:
                assert source.read(part)==exported.read(destination)
                def links(package,name):
                    rel = str(Path(name).parent / "_rels" / (Path(name).name+".rels")).replace("\\","/")
                    if rel not in package.namelist():
                        return []
                    return sorted(tuple(node.get(key) for key in ("Id","Type","Target"))
                                  for node in ET.fromstring(package.read(rel)) if node.get("TargetMode")=="External")
                assert links(source,part)==links(exported,destination)
        for name in exported.namelist():
            if name.startswith(("ppt/media/","ppt/charts/","ppt/embeddings/")) and not name.endswith(".rels"):
                assert hashlib.sha256(exported.read(name)).hexdigest() in expected_assets
                assets += 1
    return {"slide_count":len(targets),"slide_xml_byte_identical":True,"external_links_unchanged":True,
            "all_internal_relationships_resolve":True,"media_chart_embedding_parts_verified":assets}


def export_sample(db, ids, destination):
    manager = TaskManager(db)
    try:
        started = perf_counter()
        task_id = manager.submit_export(tuple(ids))
        task = manager.wait(task_id,timeout=120)
        if task["status"]!="SUCCEEDED":
            raise RuntimeError(task["error_code"])
        elapsed = round((perf_counter()-started)*1000,3)
        plan = json.loads(task["payload_json"])
        output = Path(json.loads(task["result_json"])["output_path"])
        shutil.copyfile(output,destination)
        verified = verify_export(destination,plan)
        started = perf_counter()
        assert manager.submit_export(tuple(ids))==task_id
        cached_ms = round((perf_counter()-started)*1000,3)
        return {"output":str(destination),"bytes":destination.stat().st_size,"export_ms":elapsed,
                "cache_hit_ms":cached_ms,"same_task_reused":True,**verified}
    finally:
        manager.shutdown()


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "evaluation/artifacts/stage7"
    output.mkdir(parents=True,exist_ok=True)
    report = {"scope":"Offline isolated DB/snapshot copies; shared production export workflow; no AI or PowerPoint COM",
              "samples":[]}
    with TemporaryDirectory(prefix="stage7-",dir=root / "data") as folder:
        temporary = Path(folder)
        company_db = temporary / "company/cases.sqlite3"
        company_db.parent.mkdir()
        with closing(sqlite3.connect(DB_PATH.resolve().as_uri()+"?mode=ro",uri=True)) as original,closing(sqlite3.connect(company_db)) as clone:
            original.backup(clone)
        init_db(company_db)
        with connect(company_db) as conn:
            conn.execute("DELETE FROM tasks")
            snapshots = conn.execute("SELECT DISTINCT source_snapshot_reference FROM sources WHERE source_snapshot_reference IS NOT NULL").fetchall()
            uploads = company_db.parent / "uploads"
            uploads.mkdir()
            for row in snapshots:
                source = Path(row[0])
                copied = uploads / source.name
                shutil.copyfile(source,copied)
                conn.execute("UPDATE sources SET source_snapshot_reference=? WHERE source_snapshot_reference=?",(str(copied),str(source)))
            rows = conn.execute("SELECT c.case_id,s.slide_number FROM cases c JOIN slides s ON s.case_id=c.case_id").fetchall()
            by_page = {row["slide_number"]:row["case_id"] for row in rows}
        pages = [82,81,65,64,26,19,79,80]
        sample = export_sample(company_db,[by_page[number] for number in pages],output / "company_selected.pptx")
        sample.update(name="company_selected",original_pages_in_export_order=pages)
        report["samples"].append(sample)
        db = temporary / "synthetic/cases.sqlite3"
        with patch("app.config.get_ai_settings",return_value=AISettings()),patch("app.services.ingest_service.fetch_case_page",return_value={}):
            a = ingest_deck(root / "evaluation/fixtures/source_a.pptx",db)
            b = ingest_deck(root / "evaluation/fixtures/source_b.pptx",db)
            cloud = ingest_deck(root / "evaluation/fixtures/source_a.pptx",db,source_type="SHAREPOINT",
                                original_source_url="https://example.sharepoint.com/authorized-snapshot")
            ids = [b["cases"][0].case_id,a["cases"][1].case_id,cloud["cases"][0].case_id,a["cases"][0].case_id]
            sample = export_sample(db,ids,output / "mixed_sources.pptx")
            sample.update(name="mixed_sources",includes_local_and_imported_cloud=True,includes_repeated_original_page=True)
            report["samples"].append(sample)
    (root / "evaluation/stage7_exports.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
