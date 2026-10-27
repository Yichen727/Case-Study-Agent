from pathlib import Path
import tempfile

import streamlit as st

from app.config import DB_PATH
from app.db.repository import init_db, list_cases, update_metadata
from app.search.keyword_search import search_cases
from app.services.ingest_service import ingest_deck
from app.export.slide_export import export_original_slides, SlideExportNotImplemented

st.set_page_config(page_title="Case Study Agent", layout="wide")
init_db(DB_PATH)

st.title("Case Study Agent")
st.caption("MVP scaffold: local PPTX parsing, SQLite storage and keyword search.")

with st.sidebar:
    st.header("1. Ingest master deck")
    uploaded = st.file_uploader("Upload approved PowerPoint deck", type=["pptx"])
    
    if uploaded and st.button("Parse and save deck", type="primary"):
        with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            temp_path = Path(tmp.name)
        try:
            result = ingest_deck(temp_path, DB_PATH)
            st.success(
                f"Parsed {result['slide_count']} slides; "
                f"found {result['case_count']} case-study slides."
            )
        except Exception as exc:
            st.error(f"Could not parse this PPTX: {exc}")
        finally:
            temp_path.unlink(missing_ok=True)

    st.divider()
    st.header("2. Search")
    query = st.text_input("Search cases", placeholder="e.g. sustainability, Gen Z, Client")
    limit = st.slider("Maximum results", min_value=1, max_value=20, value=10)
    search_clicked = st.button("Search", use_container_width=True)

tab_search, tab_all, tab_metadata, tab_export = st.tabs(
    ["Search results", "All cases", "Complete metadata", "Export (PoC pending)"]
)

if search_clicked:
    st.session_state["search_results"] = search_cases(str(DB_PATH), query, limit)

with tab_search:
    results = st.session_state.get("search_results", [])
    if not query and not results:
        st.info("Upload a master deck, then enter a search query in the sidebar.")
    elif query and not results:
        st.warning("No results yet. Run Search, or try a broader keyword.")
    for item in results:
        with st.container(border=True):
            st.subheader(item["title"])
            st.caption(
                f"Case ID: {item['case_id']} · Slides: {item.get('slide_numbers') or 'Unknown'} "
                f"· Status: {item['review_status']}"
            )
            st.write(f"**Pillar:** {item.get('pillar') or 'Missing'}")
            st.write(f"**Client:** {item.get('client') or 'Missing'}")
            st.write(f"**Services:** {item.get('services') or 'Missing'}")
            for section in ("brief", "creative", "impact"):
                value = item.get(section)
                if value:
                    with st.expander(section.upper()):
                        st.write(value)
            links = item.get("source_links_json") or "[]"
            link_labels = item.get("source_link_labels_json") or "[]"
            st.caption(f"Source deck: {item['source_deck']}")
            import json
            urls = json.loads(links or "[]")
            labelled_links = json.loads(link_labels or "[]")
            labels_by_url = {
                link.get("url"): link.get("text")
                for link in labelled_links
                if isinstance(link, dict) and link.get("url")
            }
            for url in urls:
                label = labels_by_url.get(url) or "Open link"
                st.markdown(f"[{label}]({url})")

with tab_all:
    cases = list_cases(DB_PATH)
    st.write(f"Stored cases: {len(cases)}")
    if cases:
        st.dataframe(
            [
                {
                    "Title": c["title"],
                    "Pillar": c["pillar"],
                    "Client": c["client"],
                    "Services": c["services"],
                    "Slides": c["slide_numbers"],
                    "Status": c["review_status"],
                }
                for c in cases
            ],
            use_container_width=True,
            hide_index=True,
        )

with tab_metadata:
    cases = list_cases(DB_PATH)
    if not cases:
        st.info("Ingest a deck first.")
    else:
        options = {f"{c['title']} ({c['case_id']})": c for c in cases}
        selected_label = st.selectbox("Choose a case", list(options.keys()))
        selected = options[selected_label]
        st.write("Fill only the fields you can verify. Values are saved with provenance.")
        with st.form("metadata_form"):
            field_name = st.selectbox("Field to update", ["client", "services", "pillar"])
            current_value = selected.get(field_name) or ""
            new_value = st.text_input("Value", value=current_value)
            source = st.selectbox(
                "Source",
                ["user_entered", "original_case_material", "authorised_external_source"],
            )
            verification = st.selectbox(
                "Verification status", ["needs_review", "confirmed", "unverified"]
            )
            editor = st.text_input("Editor ID", value="internal_user")
            save = st.form_submit_button("Save metadata")
        if save:
            try:
                update_metadata(
                    str(DB_PATH),
                    selected["case_id"],
                    field_name,
                    new_value,
                    updated_by=editor.strip() or "unknown",
                    source=source,
                    verification_status=verification,
                )
                st.success("Metadata saved. Refresh the case view to see the updated value.")
            except Exception as exc:
                st.error(str(exc))

with tab_export:
    st.warning(
        "Original-slide export is not implemented in this scaffold. "
        "Do not use reconstructed slides as a substitute for preserving the source deck."
    )
    cases = list_cases(DB_PATH)
    if cases:
        export_options = {
            f"{c['title']} ({c['case_id']})": c["case_id"] for c in cases
        }
        st.multiselect("Select cases to export", list(export_options.keys()), key="export_selection")
    if st.button("Attempt export (placeholder)"):
        try:
            selected_ids = [
                export_options[label]
                for label in st.session_state.get("export_selection", [])
            ]
            export_original_slides(selected_ids, "selected_cases.pptx")
        except SlideExportNotImplemented as exc:
            st.info(str(exc))
