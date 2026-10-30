"""Selected case ordering and export actions share one local fragment."""
import hashlib

import streamlit as st

from app.db.repository import case_options_by_ids
from app.ui.selection_state import (
    remove_cases, reorder_cases, selected_ids,
)
from app.ui.selected_case_list import selected_case_list
from app.ui.export_panel import render_export_panel


def selection_labels(cases):
    """Readable unique labels; disambiguation is stable while reordering."""
    labels,counts = {},{}
    for case in sorted(cases,key=lambda item:item["case_id"]):
        text = f"{case['title']}\n{case['source_deck']} · slide {case['slide_numbers']}"
        counts[text] = counts.get(text,0)+1
        suffix = f" · copy {counts[text]}" if counts[text]>1 else ""
        status = {"CONFIRMED":"Confirmed","NEEDS_REVIEW":"Needs Review"}.get(case.get("review_status"))
        labels[case["case_id"]] = f"{text}{suffix}" + (f"\n{status}" if status else "")
    return labels


@st.fragment
def render_selected_cases(db_path, manager, owner):
    st.subheader("Selected cases")
    ids = selected_ids(st.session_state)
    cases = case_options_by_ids(db_path,list(ids)) if ids else []
    names = selection_labels(cases)
    if any(case_id not in names for case_id in ids):
        remove_cases(st.session_state,[case_id for case_id in ids if case_id not in names])
        st.warning("Cases no longer in the library were removed from the selection.")
        ids = selected_ids(st.session_state)
    render_export_panel(db_path,manager,owner,cases)
    if not ids:
        st.info("Choose cases using the checkboxes in Search results.")
        return
    st.caption(f"{len(ids)} cases selected. Drag to reorder; Generate PPTX confirms and exports this order.")
    items = [names[case_id] for case_id in ids]
    # Reset the component after membership, order, or displayed metadata changes.
    fingerprint = hashlib.sha256("\n".join(items).encode()).hexdigest()[:12]
    revision = st.session_state.get("selection_revision",0)
    # action is a one-shot trigger; a state default would hide incoming events.
    result = selected_case_list(data={"cases":[{"id":case_id,"label":names[case_id]} for case_id in ids]},
                                on_action_change=lambda:None,
                                key=f"selected-order:{revision}:{fingerprint}")
    if action := result.action:
        if action["type"] == "remove":
            remove_cases(st.session_state,[action["case_id"]])
        elif action["type"] == "reorder":
            reorder_cases(st.session_state,action["case_ids"])
        st.rerun(scope="fragment")
