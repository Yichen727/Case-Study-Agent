"""Generate in the shared worker; read presentation bytes only on download."""
import json
from pathlib import Path

import streamlit as st

from app.db.repository import case_options_by_ids
from app.export.slide_export import CaseExportError, export_error_message
from app.services.task_service import TaskQueueFull
from app.ui.selection_panel import selection_labels
from app.ui.selection_state import export_order_snapshot, selected_ids

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def download_bytes(path):
    return Path(path).read_bytes()


@st.fragment
def render_export_panel(db_path, manager, owner, focus_selection_tab):
    st.subheader("Export original PowerPoint")
    st.caption("Original slides are copied as-is. Metadata edits are not written into the presentation.")
    snapshot = None
    try:
        snapshot = export_order_snapshot(st.session_state)
    except ValueError:
        st.info("Select cases and confirm their order in Selected cases first.")
    else:
        cases = case_options_by_ids(db_path,list(snapshot))
        if len(cases)!=len(snapshot):
            snapshot = None
            st.warning("A selected case is no longer available. Update the selection and confirm again.")
        else:
            names = selection_labels(cases)
            st.success(f"Confirmed selection: {len(snapshot)} slides.")
            with st.expander("Slide order"):
                for position,case_id in enumerate(snapshot,1):
                    st.write(f"{position}. {names[case_id].replace(chr(10),' · ')}")
            if any(case["review_status"]=="NEEDS_REVIEW" or case["unverified_ai"] for case in cases):
                st.info("Some selected cases have incomplete or AI-inferred metadata. Their original slides can still be exported.")

    previous_id = st.session_state.get("last_export_task_id")
    previous = manager.get_task(previous_id,owner) if previous_id else None
    same_running = bool(previous and previous["status"] in {"QUEUED","RUNNING"}
                        and tuple(json.loads(previous["payload_json"])["case_ids"])==snapshot)
    if st.button("Generate PPTX",type="primary",disabled=snapshot is None or same_running):
        try:
            st.session_state["last_export_task_id"] = manager.submit_export(snapshot,owner)
            st.rerun()
        except CaseExportError as exc:
            st.error(str(exc))
        except TaskQueueFull as exc:
            st.warning(str(exc))
    if st.button("Go to Selected cases",on_click=focus_selection_tab):
        st.rerun()

    if previous is None:
        return
    plan = json.loads(previous["payload_json"])
    if previous["status"] in {"QUEUED","RUNNING"}:
        st.info("Preparing your presentation in the background. You can keep searching or reviewing cases.")
    elif previous["status"] == "SUCCEEDED":
        result = json.loads(previous["result_json"])
        path = Path(result["output_path"]).resolve()
        if path.parent!=Path(db_path).resolve().parent / "exports" or not path.is_file():
            st.info("This generated file is no longer available. Generate it again.")
            return
        if tuple(plan["case_ids"])!=selected_ids(st.session_state):
            st.info("This download uses the selection fixed when generation started. Your current selection is different.")
        st.success(f"Presentation ready: {result['slide_count']} slides.")
        with st.expander("Slides in this file"):
            for index,case in enumerate(plan["cases"],1):
                st.write(f"{index}. {case['title']} · {case['source_deck']} · slide {case['slide_numbers']}")
        st.download_button("Download PPTX",data=lambda:download_bytes(path),mime=PPTX_MIME,
                           file_name=f"case-studies-{result['slide_count']}-slides.pptx",on_click="ignore")
    elif previous["status"] == "INTERRUPTED":
        st.warning("Export stopped when the app restarted. Retry it from Processing, or generate your current selection again.")
    else:
        st.error(export_error_message(previous["error_code"]))
