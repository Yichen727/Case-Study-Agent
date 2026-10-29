"""A compact metadata form; completeness determines status automatically."""
import json

import streamlit as st

from app.db.repository import get_case_text, get_field_metadata, update_case_metadata
from app.models import REQUIRED_CASE_FIELDS, missing_value, case_display_status

FIELD_LABELS = {
    "title":"Case name", "pillar":"Pillar", "client":"Client", "services":"Services",
    "brief":"Brief", "creative":"Creative", "impact":"Impact",
}


def render_review(db_path, selected, focus_metadata_tab):
    case_id = selected["case_id"]
    st.caption(f"Status: {case_display_status(selected)}")
    missing = [FIELD_LABELS[name] for name in REQUIRED_CASE_FIELDS if missing_value(selected.get(name))]
    if missing:
        st.caption("Missing: " + ", ".join(missing))

    # Refresh untouched values after background enrichment, while retaining drafts.
    baseline_key = f"_metadata_baseline:{case_id}"
    baseline = st.session_state.get(baseline_key,{})
    current = {name:selected.get(name) or "" for name in REQUIRED_CASE_FIELDS}
    for name,value in current.items():
        key = f"metadata_value:{case_id}:{name}"
        if key not in st.session_state or st.session_state[key] == baseline.get(name):
            st.session_state[key] = value
    st.session_state[baseline_key] = current

    with st.form(f"metadata_form:{case_id}"):
        values = {}
        left,right = st.columns(2)
        with left:
            values["client"] = st.text_input("Client",key=f"metadata_value:{case_id}:client",placeholder="Unknown")
        with right:
            values["services"] = st.text_input("Services",key=f"metadata_value:{case_id}:services",placeholder="Unknown")
        with st.expander("Other metadata"):
            for name in ("title","pillar","brief","creative","impact"):
                widget = st.text_area if name in {"brief","creative","impact"} else st.text_input
                values[name] = widget(FIELD_LABELS[name],key=f"metadata_value:{case_id}:{name}")
        saved = st.form_submit_button("Save metadata",on_click=focus_metadata_tab)
    if saved:
        updates = {name:value for name,value in values.items() if value.strip() != current[name].strip()}
        try:
            update_case_metadata(db_path,case_id,updates)
            st.session_state["notice"] = "Metadata saved. Status updated automatically."
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            st.error("Metadata could not be saved. Please try again.")

    with st.expander("Original text and sources"):
        st.text(get_case_text(db_path,case_id) or "Original text unavailable.")
        for index,url in enumerate(json.loads(selected.get("source_links_json") or "[]"),1):
            st.markdown(f"[Original link {index}]({url})")
        if selected.get("linked_page_text"):
            st.text(selected["linked_page_text"])
        origins = {"SLIDE_EXTRACTED":"Original slide", "LINK_EXTRACTED":"Linked page",
                   "AI_INFERRED":"AI supplemented", "HUMAN_ENTERED":"Manual edit"}
        rows = []
        for row in get_field_metadata(db_path,case_id):
            name = row["field_name"]
            if name not in REQUIRED_CASE_FIELDS:
                continue
            reference = row["evidence_reference"] or ""
            if reference.startswith(("source:","ai:")):
                reference = f"{selected['source_deck']} · slide {selected['slide_numbers']}"
            elif reference.startswith("review:"):
                reference = "Manual edit"
            rows.append({"Field":FIELD_LABELS[name],"Source":origins.get(row["source_kind"],"Unknown"),
                         "Evidence":row["evidence_text"] or reference,"Model":row["model"]})
        st.dataframe(rows,hide_index=True)
