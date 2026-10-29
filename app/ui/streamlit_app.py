from collections import Counter
import json
import logging
from math import ceil

import streamlit as st

from app.config import DB_PATH, get_ai_settings
from app.db.migrations import SCHEMA_VERSION
from app.db.repository import (
    case_options_by_ids, count_cases, data_version, get_case,
    init_db, list_case_options, list_cases, list_metadata_options,
)
from app.ingestion.errors import IngestionError
from app.models import case_display_status
from app.search.keyword_search import search_cases
from app.services.source_service import remove_unused_snapshots, unused_snapshots
from app.services.task_service import get_task_manager, TaskQueueFull
from app.ui.review_panel import render_review

PAGE_SIZE = 25
st.set_page_config(page_title="Case Study Agent", layout="wide")

initialized = init_db(DB_PATH)
if initialized or st.session_state.get("schema_version") != SCHEMA_VERSION:
    for key in ("search_results", "search_request", "metadata_case_id", "export_case_ids",
                "active_tab", "all_page", "metadata_page", "export_page", "_metadata_case", "_metadata_field",
                "metadata_show_all", "_metadata_show_all"):
        st.session_state.pop(key, None)
    st.session_state["schema_version"] = SCHEMA_VERSION
current_version = data_version(DB_PATH)
if st.session_state.get("data_version") != current_version:
    st.session_state.pop("search_results", None)
    st.session_state["data_version"] = current_version

manager = get_task_manager(DB_PATH)
# Local Windows demo has one operator. Phase 8 will supply the signed-in identity;
# Microsoft tokens will not be stored in task payloads or the public-page cache.
owner = "local"


def focus_metadata_tab():
    st.session_state["active_tab"] = "Needs Review"


def labels(options):
    result = {case["case_id"]: f"{case['title']} · {case['source_deck']} · slide {case['slide_numbers']}"
              for case in options}
    counts = Counter(result.values())
    copies = Counter()
    for case_id, label in result.items():
        if counts[label] > 1:
            copies[label] += 1
            result[case_id] = f"{label} · copy {copies[label]}"
    return result


def page_offset(key, total):
    pages = max(1, ceil(total / PAGE_SIZE))
    page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1, key=key)
    st.caption(f"Page {page} of {pages} · {total} cases")
    return (page - 1) * PAGE_SIZE


def case_options(key, retained):
    options = list_case_options(DB_PATH, PAGE_SIZE, page_offset(key, count_cases(DB_PATH)))
    present = {case["case_id"] for case in options}
    options += case_options_by_ids(DB_PATH, [case_id for case_id in retained if case_id not in present])
    return options


def show_case(item):
    with st.container(border=True):
        st.subheader(item["title"])
        st.caption(f"Source: {item['source_deck']} · Slides: {item.get('slide_numbers') or 'Unknown'} "
                   f"· Status: {case_display_status(item)}")
        if item["requires_reimport"]:
            st.info("Original snapshot missing for this legacy record. Re-import the original deck.")
        for field in ("pillar", "client", "services"):
            st.write(f"**{field.title()}:** {display_field(item, field)}")
        for field in ("brief", "creative", "impact"):
            if item.get(field):
                with st.expander(field.upper()):
                    st.write(item[field])
        urls = json.loads(item.get("source_links_json") or "[]")
        records = json.loads(item.get("source_link_labels_json") or "[]")
        link_types = sorted({link.get("link_type", "OTHER") for link in records})
        st.caption("Link type: " + (", ".join(link_types) if link_types else "NONE"))
        by_url = {link["url"]: link.get("text") for link in records if link.get("url")}
        for url in urls:
            st.markdown(f"[{by_url.get(url) or 'Open link'}]({url})")


def display_field(case, field):
    value = case.get(field)
    return value or "Unknown"


st.title("Case Study Agent")
st.caption("Import presentations in the background; search and review existing cases while they process.")
if notice := st.session_state.pop("notice", None):
    st.success(notice)

with st.sidebar:
    st.header("1. Import presentations")
    uploaded = st.file_uploader(
        "Upload approved PowerPoint decks", type=["pptx"], accept_multiple_files=True,
        key=f"uploads:{st.session_state.get('uploader_version', 0)}",
    )
    if uploaded and st.button("Parse and save decks", type="primary"):
        submitted = 0
        for index, upload in enumerate(uploaded):
            try:
                manager.submit_import(upload, upload.name, owner)
                submitted += 1
            except TaskQueueFull as exc:
                st.session_state["notice"] = (
                    f"{submitted} files queued; {len(uploaded) - index} were not queued. "
                    "Please select the remaining files again after an import finishes."
                )
                break
            except IngestionError as exc:
                st.error(f"{upload.name}: {exc.user_message()}")
            except Exception as exc:
                logging.getLogger(__name__).warning("Could not queue import: %s", type(exc).__name__)
                st.error(f"Could not queue {upload.name}. Please try again.")
        if submitted:
            st.session_state["uploader_version"] = st.session_state.get("uploader_version", 0) + 1
            st.rerun()

    initial_jobs = manager.list_tasks(owner)
    polling = any(job["status"] in {"QUEUED", "RUNNING"} for job in initial_jobs)

    @st.fragment(run_every=1.0 if polling else None)
    def show_tasks():
        jobs = manager.list_tasks(owner)
        if jobs:
            st.subheader("Processing")
        for job in jobs:
            st.write(job["filename"])
            if job["status"] in {"QUEUED", "RUNNING"}:
                st.progress(min(1.0, job["completed"] / max(1, job["total"])),
                            text=f"{job['stage']} ({job['completed']}/{job['total']})")
            elif job["status"] == "SUCCEEDED":
                result = json.loads(job["result_json"])
                if job["kind"] == "IMPORT":
                    st.success(f"Saved {result['case_count']} case-study slides.")
                else:
                    st.success("Task complete.")
                if result.get("enrichment", {}).get("unavailable_pages"):
                    st.caption("Some web metadata is unavailable. Cases remain ready for review.")
                ai = result.get("ai", {})
                if ai.get("status") in {"FAILED", "PARTIAL"}:
                    messages = {"AI_AUTH_FAILED": "Check the local OpenAI key and model permissions.",
                                "AI_RATE_LIMITED": "OpenAI rate or quota limit reached. Retry later.",
                                "AI_CASE_LIMIT": "Some cases remain for another AI task."}
                    st.warning(messages.get(ai.get("error_code"), "AI metadata could not finish. Cases remain ready for review."))
                elif ai.get("status") == "COMPLETED":
                    st.caption(f"AI filled {ai['applied_fields']} fields; {ai['unknown_fields']} remain Unknown.")
            else:
                if job["status"] == "INTERRUPTED":
                    st.warning("Processing stopped when the app restarted. Retry to continue.")
                elif job["error_code"] in {"INVALID_FILE", "FETCH_FAILED", "PARSE_FAILED"}:
                    st.error(IngestionError(job["error_code"]).user_message())
                else:
                    st.error("This task could not be completed. Please try again.")
                if st.button("Retry", key=f"retry:{job['task_id']}"):
                    try:
                        if not manager.retry(job["task_id"], owner):
                            st.info("Please upload the file again.")
                        else:
                            st.rerun()
                    except TaskQueueFull as exc:
                        st.warning(str(exc))
        if jobs and st.button("Clear finished tasks"):
            manager.clear_finished(owner)
            st.rerun()
        active = any(job["status"] in {"QUEUED", "RUNNING"} for job in jobs)
        if data_version(DB_PATH) != st.session_state.get("data_version") or (polling and not active):
            st.rerun()

    show_tasks()
    st.divider()
    st.header("2. Search")
    with st.form("case_search", enter_to_submit=True):
        query = st.text_input("Search cases", placeholder="e.g. sustainability, Gen Z, Client")
        limit = st.slider("Maximum results", 1, 20, 10)
        search_clicked = st.form_submit_button("Search", width="stretch")
    if search_clicked:
        st.session_state["search_request"] = {"query": query, "limit": limit}
        st.session_state.pop("search_results", None)
        st.session_state["active_tab"] = "Search results"

    with st.expander("Local storage"):
        st.caption("Identical decks share snapshot bytes; imports remain separate. Backups and active tasks are protected.")
        if st.button("Check unused snapshots"):
            try:
                candidates = unused_snapshots(DB_PATH)
                st.session_state["unused_storage"] = (
                    len(candidates), sum(path.stat().st_size for path in candidates) / 1024**2
                )
            except RuntimeError as exc:
                st.error(str(exc))
        if storage := st.session_state.get("unused_storage"):
            count, mib = storage
            st.caption(f"{count} unused files · {mib:.1f} MiB")
            if count and st.button("Remove unused snapshots"):
                try:
                    removed = remove_unused_snapshots(DB_PATH)
                    st.session_state.pop("unused_storage", None)
                    st.session_state["notice"] = f"Removed {removed} unused snapshot files."
                    st.rerun()
                except (RuntimeError, OSError):
                    st.error("Unused snapshots could not be removed. Please check again and retry.")

tab_search, tab_all, tab_metadata, tab_export = st.tabs(
    ["Search results", "All cases", "Needs Review", "Export (PoC pending)"],
    key="active_tab", on_change="rerun",
)

if tab_search.open:
    with tab_search:
        request = st.session_state.get("search_request")
        if request and "search_results" not in st.session_state:
            st.session_state["search_results"] = search_cases(str(DB_PATH), request["query"], request["limit"])
        results = st.session_state.get("search_results", [])
        if not request:
            st.info("Upload a presentation, then enter a query and press Enter or Search.")
        elif not results:
            st.warning("No matching cases. Try a broader keyword.")
        for item in results:
            show_case(item)

if tab_all.open:
    with tab_all:
        total = count_cases(DB_PATH)
        st.write(f"Stored cases: {total}")
        if total:
            cases = list_cases(DB_PATH, PAGE_SIZE, page_offset("all_page", total))
            if any(case["requires_reimport"] for case in cases):
                st.info("Some legacy cases on this page need their original decks re-imported.")
            st.dataframe([{
                "Title": case["title"], "Source deck": case["source_deck"], "Slides": case["slide_numbers"],
                "Pillar": case["pillar"], "Client": display_field(case,"client"), "Services": display_field(case,"services"),
                "Status": case_display_status(case),
            } for case in cases], width="stretch", hide_index=True)
        else:
            st.info("Import a presentation to start your case library.")

if tab_metadata.open:
    with tab_metadata:
        if not count_cases(DB_PATH):
            st.info("Import a presentation first.")
        else:
            show_all = st.checkbox("Show all cases",value=st.session_state.get("_metadata_show_all",False),
                                   key="metadata_show_all")
            st.session_state["_metadata_show_all"] = show_all
            options = list_metadata_options(DB_PATH,show_all=show_all,
                                            retained_case_id=st.session_state.get("_metadata_case"))
            if not options:
                st.success("All cases have complete metadata. Show all cases to make corrections.")
            else:
                names = labels(options)
                retained = st.session_state.get("_metadata_case")
                if "metadata_case_id" not in st.session_state and retained in names:
                    st.session_state["metadata_case_id"] = retained
                selected_id = st.selectbox("Choose a case",list(names),format_func=names.__getitem__,key="metadata_case_id")
                st.session_state["_metadata_case"] = selected_id
                selected = get_case(DB_PATH,selected_id)
                render_review(DB_PATH,selected,focus_metadata_tab)
                with st.expander("Retry automatic enrichment"):
                    settings = get_ai_settings()
                    if not settings.api_key:
                        st.caption("Set OPENAI_API_KEY in the local .env file to enable AI enrichment.")
                    if st.button("Retry AI enrichment",disabled=not bool(settings.api_key),
                                 help="Fill missing Client / Services for this presentation. Existing values are retained."):
                        try:
                            manager.submit_ai_enrichment(selected["source_id"],selected["source_deck"],owner)
                            st.rerun()
                        except TaskQueueFull as exc:
                            st.warning(str(exc))
                    if st.button("Retry web metadata"):
                        try:
                            manager.submit_enrichment(selected["source_id"],selected["source_deck"],owner)
                            st.rerun()
                        except TaskQueueFull as exc:
                            st.warning(str(exc))

if tab_export.open:
    with tab_export:
        st.info("Original PPTX samples have passed validation. Exporting selected cases will be connected in phase 7.")
        if count_cases(DB_PATH):
            retained = st.session_state.get("_export_cases", [])
            options = case_options("export_page", retained)
            names = labels(options)
            if "export_case_ids" not in st.session_state:
                st.session_state["export_case_ids"] = [case_id for case_id in retained if case_id in names]
            selected = st.multiselect("Select cases to export", list(names), format_func=names.__getitem__, key="export_case_ids")
            st.session_state["_export_cases"] = selected
