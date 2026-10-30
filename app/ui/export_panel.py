"""Generate in the shared worker; read presentation bytes only on download."""
import json
from pathlib import Path

import streamlit as st

from app.export.slide_export import CaseExportError, export_error_message
from app.services.task_service import TaskQueueFull
from app.ui.selection_state import export_order_snapshot, selected_ids

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def download_bytes(path):
    return Path(path).read_bytes()


def render_export_panel(db_path, manager, owner, cases):
    """Render inside the selection fragment so ordering changes update export too."""
    snapshot = selected_ids(st.session_state)
    previous_id = st.session_state.get("last_export_task_id")
    previous = manager.get_task(previous_id,owner) if previous_id else None
    same_running = bool(previous and previous["status"] in {"QUEUED","RUNNING"}
                        and tuple(json.loads(previous["payload_json"])["case_ids"])==snapshot)
    result, path = None, None
    if previous and previous["status"]=="SUCCEEDED":
        result = json.loads(previous["result_json"])
        candidate = Path(result["output_path"]).resolve()
        if candidate.parent==Path(db_path).resolve().parent / "exports" and candidate.is_file():
            path = candidate
    generate, download, _ = st.columns([1,1,3])
    with generate:
        if st.button("Generate PPTX",type="primary",disabled=not snapshot or len(cases)!=len(snapshot) or same_running,
                     help="Confirms the current slide order and generates the original editable PPTX."):
            try:
                task_id = manager.submit_export(export_order_snapshot(st.session_state),owner)
                st.session_state["last_export_task_id"] = task_id
                st.rerun()
            except CaseExportError as exc:
                st.error(str(exc))
            except TaskQueueFull as exc:
                st.warning(str(exc))
    with download:
        st.download_button("Download PPTX",data=(lambda:download_bytes(path)) if path else b"",mime=PPTX_MIME,
                           file_name=f"case-studies-{result['slide_count']}-slides.pptx" if path else "case-studies.pptx",
                           disabled=path is None,on_click="ignore")
    if any(case["review_status"]=="NEEDS_REVIEW" or case["unverified_ai"] for case in cases):
        st.caption("Some selected metadata is incomplete or AI-inferred; the original slides can still be exported.")

    if previous is None:
        return
    plan = json.loads(previous["payload_json"])
    if previous["status"] in {"QUEUED","RUNNING"}:
        st.info("Preparing your presentation in the background. You can keep searching or reviewing cases.")
    elif previous["status"] == "SUCCEEDED":
        if path is None:
            st.info("This generated file is no longer available. Generate it again.")
            return
        different_selection = tuple(plan["case_ids"])!=selected_ids(st.session_state)
        if different_selection:
            st.info("This download uses the selection fixed when generation started. Your current selection is different.")
        st.success(f"Presentation ready: {result['slide_count']} slides.")
        if different_selection:
            with st.expander("Slides in this file"):
                for index,case in enumerate(plan["cases"],1):
                    st.write(f"{index}. {case['title']} · {case['source_deck']} · slide {case['slide_numbers']}")
    elif previous["status"] == "INTERRUPTED":
        st.warning("Export stopped when the app restarted. Retry it from Processing, or generate your current selection again.")
    else:
        st.error(export_error_message(previous["error_code"]))
