"""Selected case ordering; drag/remove/confirm rerun only this local fragment."""
import hashlib

import streamlit as st

from app.db.repository import case_options_by_ids
from app.ui.selection_state import (
    confirm_order, export_order_snapshot, remove_cases, reorder_cases, selected_ids,
)
from app.ui.selected_case_list import selected_case_list


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
def render_selected_cases(db_path):
    st.subheader("Selected cases")
    ids = selected_ids(st.session_state)
    if not ids:
        st.info("Choose cases using the checkboxes in Search results.")
        return
    cases = case_options_by_ids(db_path,list(ids))
    names = selection_labels(cases)
    if any(case_id not in names for case_id in ids):
        remove_cases(st.session_state,[case_id for case_id in ids if case_id not in names])
        st.warning("Cases no longer in the library were removed from the selection.")
        ids = selected_ids(st.session_state)
        if not ids:
            return
    st.caption(f"{len(ids)} cases selected. Drag to set the final slide order.")
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

    st.button("Confirm order",type="primary",on_click=confirm_order,args=(st.session_state,))
    if st.session_state.get("confirmed_case_ids") is not None:
        snapshot = export_order_snapshot(st.session_state)
        st.success(f"Order confirmed for {len(snapshot)} slides.")
    else:
        st.caption("Confirm the order when ready. Adding, removing or reordering cases requires a new confirmation.")
