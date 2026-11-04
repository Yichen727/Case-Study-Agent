"""One new-case editor; network and PPTX work run through the existing worker."""
from pathlib import Path

from pydantic import ValidationError
import streamlit as st

from app.config import get_ai_settings
from app.generation.models import CASE_FIELDS, Content, clean_text
from app.generation.storage import DraftStorageError, DraftChangedError
from app.services.case_generation import GenerationError, ERROR_MESSAGES as GENERATION_ERRORS
from app.services.task_service import TaskQueueFull
from app.export.generated_case_export import (
    GeneratedExportError, ERROR_MESSAGES as EXPORT_ERRORS, ENGINE_VERSION, content_hash, template_hash,
)
from app.ui.export_panel import PPTX_MIME, download_bytes

ERRORS = {**GENERATION_ERRORS, **EXPORT_ERRORS, "DRAFT_STORAGE_FAILED": str(DraftStorageError()),
          "DRAFT_CHANGED": str(DraftChangedError())}


def case_error_message(code):
    return ERRORS.get(code, "This case task could not be completed. Your saved draft is retained; please try again.")


def _remember_input(key, canonical):
    st.session_state[canonical] = st.session_state[key]


def _capture_edit(name, key):
    editor = st.session_state["_case_editor"]
    editor["values"][name] = st.session_state[key]
    if clean_text(editor["values"][name]) == clean_text(editor["baseline"][name]):
        editor["dirty"].discard(name)
    else:
        editor["dirty"].add(name)


def _editor(draft):
    editor = st.session_state.get("_case_editor")
    baseline = {name: getattr(draft.content, name) or "" for name in CASE_FIELDS}
    if not editor or editor["draft_id"] != draft.draft_id:
        editor = {"draft_id": draft.draft_id, "revision": draft.revision, "values": dict(baseline),
                  "baseline": dict(baseline), "dirty": set()}
        st.session_state["_case_editor"] = editor
    if editor["revision"] != draft.revision:
        for name in CASE_FIELDS:
            if name not in editor["dirty"] or clean_text(editor["values"][name]) == clean_text(baseline[name]):
                editor["values"][name] = baseline[name]
                editor["dirty"].discard(name)
        editor["revision"], editor["baseline"] = draft.revision, dict(baseline)
    for name in CASE_FIELDS:
        # Canonical buffers survive Streamlit cleaning up widgets on inactive tabs.
        st.session_state[f"create:{draft.draft_id}:{name}"] = editor["values"][name]
    return editor


def _save_editor(manager, draft, editor):
    changes = {name: editor["values"][name] for name in editor["dirty"]}
    return manager.drafts.edit(draft.draft_id, changes) if changes else manager.drafts.load(draft.draft_id)


def _download_path(manager, draft, content, dirty):
    record = manager.drafts.last_export(draft.draft_id)
    if not record:
        return None, False
    candidate = Path(record["output_path"]).resolve()
    try:
        current_template_hash = template_hash()
    except GeneratedExportError:
        return None, True
    stale = (dirty or record["content_hash"] != content_hash(content)
             or record["engine_version"] != ENGINE_VERSION or record["template_sha256"] != current_template_hash)
    if stale or candidate.parent != manager.drafts.folder(draft.draft_id) / "exports" or not candidate.is_file():
        return None, True
    return candidate, False


def _clear_case_buffers():
    for key in ("_case_editor", "_create_description", "_create_reply", "create_description"):
        st.session_state.pop(key, None)


def _render_history(manager, owner, current):
    entries = manager.drafts.history()
    with st.expander(f"Saved drafts ({len(entries)})"):
        if not entries:
            st.caption("No saved drafts yet.")
            return
        ids = [entry["draft_id"] for entry in entries]
        labels = {entry["draft_id"]: f"{entry['title']} · {entry['client']} · {entry['updated_at'][:19].replace('T', ' ')} UTC"
                  for entry in entries}
        if st.session_state.get("draft-history") not in ids:
            st.session_state.pop("draft-history", None)
        index = ids.index(current.draft_id) if current and current.draft_id in ids else 0
        selected = st.selectbox("Choose a saved draft", ids, index=index, format_func=labels.__getitem__, key="draft-history")
        busy = manager.case_task(selected, owner, active_only=True) is not None
        opened, deleted, _ = st.columns([1, 1, 3])
        if opened.button("Open draft", key="history-open",
                         help="Saves current field edits before opening the selected draft. No AI call is made."):
            editor = st.session_state.get("_case_editor")
            if current and editor and editor["draft_id"] == current.draft_id and editor["dirty"]:
                _save_editor(manager, current, editor)
            manager.drafts.open(selected)
            _clear_case_buffers()
            st.session_state["_case_notice"] = "Saved draft opened."
            st.rerun()
        if deleted.button("Delete draft", key="history-delete", disabled=busy,
                          help="Permanently deletes the selected draft and its generated PPTX files."):
            manager.delete_case_draft(selected)
            if current and current.draft_id == selected:
                _clear_case_buffers()
            st.session_state.pop("draft-history", None)
            st.session_state["_case_notice"] = "Draft and its generated files deleted."
            st.rerun()
        st.caption("Opening saves current field edits. Deleting permanently removes the selected draft and its generated files.")
        if busy:
            st.caption("This draft is being processed. Delete it after the task finishes.")


def _render(manager, owner):
    draft = manager.drafts.current()
    title, reset = st.columns([4, 1])
    with title:
        st.subheader("Create case")
    with reset:
        if st.button("Start new case", key="create-new"):
            manager.drafts.start_new()
            _clear_case_buffers()
            st.rerun()
    st.caption("Draft an English case from your project notes. Missing facts stay blank; images and video are left empty.")
    if notice := st.session_state.pop("_case_notice", None):
        st.success(notice)
    _render_history(manager, owner, draft)
    settings = get_ai_settings()
    if not settings.api_key:
        st.info("Ask the project owner to configure the OpenAI API key to write a new case. Saved drafts can still be edited and exported.")
    ai_task = manager.case_task(draft.draft_id, owner, kind="CASE_GENERATE", active_only=True) if draft else None
    busy = ai_task is not None
    if not draft or not draft.request_count:
        if "create_description" not in st.session_state:
            st.session_state["create_description"] = st.session_state.get("_create_description", draft.original_description if draft else "")
        description = st.text_area("Describe your project", key="create_description", height=200, max_chars=12000,
                                   on_change=_remember_input, args=("create_description", "_create_description"), disabled=busy,
                                   placeholder="Who was the client? What was the challenge, creative approach and actual outcome?")
        if st.button("Create draft", key="create-submit", type="primary", disabled=busy or not settings.api_key):
            if not description.strip():
                st.warning("Describe your project before creating a draft.")
                return
            draft = manager.drafts.create(description.strip())
            manager.submit_case_generation(draft.draft_id, owner=owner)
            st.rerun()
        if busy:
            st.info("Writing your draft in the background. You can use the other tabs while it runs.")
        return

    with st.expander("Your project notes"):
        st.text(draft.original_description)
        if draft.clarification:
            st.text(draft.clarification)
    if draft.clarification_question:
        with st.chat_message("assistant"):
            st.write(draft.clarification_question)
        reply_key = f"create_reply:{draft.draft_id}"
        if reply_key not in st.session_state:
            st.session_state[reply_key] = st.session_state.get("_create_reply", "")
        reply = st.text_area("Additional details", key=reply_key, height=110, max_chars=6000, disabled=busy,
                            on_change=_remember_input, args=(reply_key, "_create_reply"))
        submit, skip, _ = st.columns([1, 1, 3])
        if submit.button("Submit details", key="create-reply", disabled=busy or not settings.api_key):
            if not reply.strip():
                st.warning("Enter additional details, or choose Skip and continue.")
                return
            manager.submit_case_generation(draft.draft_id, reply.strip(), owner)
            st.rerun()
        if skip.button("Skip and continue", key="create-skip", disabled=busy):
            manager.skip_case_details(draft.draft_id, owner)
            st.rerun()
    if busy:
        st.info("Updating your draft in the background. Saved manual edits will be retained.")

    editor = _editor(draft)
    st.text_input("Title", key=f"create:{draft.draft_id}:title", max_chars=160, on_change=_capture_edit,
                  args=("title", f"create:{draft.draft_id}:title"))
    client, services, pillar = st.columns([1, 2, 1])
    for container, name in ((client, "client"), (services, "services"), (pillar, "pillar")):
        with container:
            st.text_input(name.title(), key=f"create:{draft.draft_id}:{name}", placeholder="Unknown",
                          max_chars={"client": 200, "services": 600, "pillar": 200}[name],
                          on_change=_capture_edit, args=(name, f"create:{draft.draft_id}:{name}"))
    for name in ("brief", "creative", "impact"):
        st.text_area(name.upper(), key=f"create:{draft.draft_id}:{name}", height=135, max_chars=6000, placeholder="Not provided",
                     on_change=_capture_edit, args=(name, f"create:{draft.draft_id}:{name}"))
    content = Content(**editor["values"])
    if editor["dirty"]:
        st.caption("Unsaved edits. Save draft or Generate PPTX to keep them.")
    st.caption(f"Body: {content.body_words} words · target 120–250 total. Client and Services are saved with the draft and do not appear on the slide.")
    if content.missing_fields:
        st.caption("Missing: " + ", ".join(name.title() for name in content.missing_fields))
    if content.body_words < 120:
        st.warning("Insufficient information: the body has fewer than 120 words. You can keep it short rather than invent facts.")
    if content.body_words > 250:
        st.warning("The body exceeds 250 words. Shorten it before generating the slide.")
    if content.title and len(content.title) > 40:
        st.warning("Shorten the title to 40 characters or fewer to fit the template.")
    exporting = manager.case_task(draft.draft_id, owner, kind="CASE_EXPORT", active_only=True)
    path, stale = _download_path(manager, draft, content, bool(editor["dirty"]))
    save, generate, download = st.columns(3)
    if save.button("Save draft", key="create-save"):
        _save_editor(manager, draft, editor)
        st.session_state["_case_notice"] = "Draft saved."
        st.rerun()
    if generate.button("Generate PPTX", key="create-generate-pptx", type="primary", disabled=busy or bool(exporting)):
        _save_editor(manager, draft, editor)
        manager.submit_case_export(draft.draft_id, owner)
        st.rerun()
    with download:
        st.download_button("Download PPTX", data=(lambda: download_bytes(path)) if path else b"", mime=PPTX_MIME,
                           file_name="new-case-study.pptx", key="create-download", disabled=path is None, on_click="ignore",
                           help="Downloads the last generated wording. Generate again after editing.")
    if exporting:
        st.info("Preparing your one-slide presentation in the background.")
    elif path:
        st.success("Your single-slide PPTX is ready.")
    elif stale:
        st.info("Your draft or template has changed. Generate a new PPTX before downloading.")
    st.caption("Check the wording before generating. Drafts are saved locally and restored when you reopen Create case.")


def render_create_case(manager, owner):
    try:
        _render(manager, owner)
    except TaskQueueFull as exc:
        st.warning(str(exc))
    except (GenerationError, DraftStorageError, DraftChangedError, GeneratedExportError) as exc:
        st.error(str(exc))
    except ValidationError:
        st.error("Check the draft field lengths. Your previous saved draft is retained.")
