"""Open and render the prepared PoC in installed Windows PowerPoint, read-only.

Requires the local evaluation environment's pywin32 (not an application dependency).
Does not start a visible presentation window or save changes to source files.
"""
from pathlib import Path
import json
import shutil
from tempfile import TemporaryDirectory
from time import perf_counter

from PIL import Image, ImageChops
import pythoncom
import win32com.client
import pywintypes


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "evaluation/stage2_export.json").read_text(encoding="utf-8"))
    folder = root / "evaluation/artifacts/stage2/rendered"
    folder.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    powerpoint = None
    opened = {}
    report = {"application": "Microsoft PowerPoint", "with_window": False, "read_only": True,
              "private_file_copies": True, "open_and_repair": False, "slides": []}
    previous_alerts = None
    scratch = TemporaryDirectory(prefix="powerpoint-check-", dir=folder.parent, ignore_cleanup_errors=True)
    assert Path(scratch.name).resolve().is_relative_to(folder.parent.resolve())
    try:
        powerpoint = win32com.client.Dispatch("PowerPoint.Application")
        report["version"] = str(powerpoint.Version)
        previous_alerts = powerpoint.DisplayAlerts
        powerpoint.DisplayAlerts = 1

        def open_deck(path):
            if path not in opened:
                copied = Path(scratch.name) / f"presentation-{len(opened)}.pptx"
                shutil.copyfile(path, copied)
                opened[path] = powerpoint.Presentations.Open2007(str(copied), -1, 0, 0, 0)
            return opened[path]

        for case in reversed(manifest["presentations"]):
            started = perf_counter()
            exported = open_deck(case["output_path"])
            print("Opened", case["name"], flush=True)
            assert exported.Slides.Count == case["slide_count"]
            for item in case["slides"]:
                source = open_deck(item["source_path"])
                page = item["output_page"]
                prefix = f"{case['name']}-{page:02d}"
                source_png = folder / f"{prefix}-original.png"
                output_png = folder / f"{prefix}-exported.png"
                width = 1280
                height = round(width * source.PageSetup.SlideHeight / source.PageSetup.SlideWidth)
                source.Slides(item["source_page"]).Export(str(source_png), "PNG", width, height)
                exported.Slides(page).Export(str(output_png), "PNG", width, height)
                with Image.open(source_png) as original, Image.open(output_png) as result:
                    assert original.size == result.size
                    difference = ImageChops.difference(original.convert("RGB"), result.convert("RGB"))
                    maximum = max(channel[1] for channel in difference.getextrema())
                original_slide = source.Slides(item["source_page"])
                result_slide = exported.Slides(page)
                report["slides"].append({
                    "presentation": case["name"], "source_page": item["source_page"], "output_page": page,
                    "max_channel_difference": maximum, "render_pixel_identical": maximum == 0,
                    "original_shape_count": original_slide.Shapes.Count, "exported_shape_count": result_slide.Shapes.Count,
                    "original_png": str(source_png), "exported_png": str(output_png),
                })
                print(case["name"], "page", page, "max render difference", maximum, flush=True)
            case["powerpoint_validation_seconds"] = round(perf_counter() - started, 3)
        report["presentations"] = [{"name": case["name"], "validation_seconds": case["powerpoint_validation_seconds"]}
                                   for case in manifest["presentations"]]
    finally:
        for presentation in reversed(list(opened.values())):
            try:
                presentation.Close()
            except pywintypes.com_error:
                print("PowerPoint could not close a validation copy.", flush=True)
        if powerpoint is not None and previous_alerts is not None:
            powerpoint.DisplayAlerts = previous_alerts
        if powerpoint is not None and powerpoint.Presentations.Count == 0:
            powerpoint.Quit()
        scratch.cleanup()
        pythoncom.CoUninitialize()
    report["all_renders_identical"] = all(item["render_pixel_identical"] for item in report["slides"])
    (root / "evaluation/stage2_powerpoint.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"slides_checked": len(report["slides"]), "all_renders_identical": report["all_renders_identical"]}))


if __name__ == "__main__":
    main()
