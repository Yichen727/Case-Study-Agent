"""One new-case editor; network and PPTX work run through the existing worker."""
from base64 import b64encode
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError
import streamlit as st

from app.config import get_ai_settings
from app.generation.models import CASE_FIELDS, Content, clean_text
from app.generation.images import CaseImageError, images_hash, prepare_images
from app.generation.links import CaseLinkError, extract_links, link_hash
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
             or record.get("request_count") != draft.request_count
             or record.get("images_hash") != images_hash(draft.images)
             or record.get("link_hash") != link_hash(draft.selected_link)
             or record["engine_version"] != ENGINE_VERSION or record["template_sha256"] != current_template_hash)
    if stale or candidate.parent != manager.drafts.folder(draft.draft_id) / "exports" or not candidate.is_file():
        return None, True
    return candidate, False


def _clear_case_buffers():
    for key in ("_case_editor", "_create_description", "_create_reply", "_create_reply_round", "create_description",
                "_case_pending_images", "_case_image_error", "_case_pending_link_choice", "_case_link_error"):
        st.session_state.pop(key, None)
    st.session_state["_case_image_epoch"] = st.session_state.get("_case_image_epoch", 0) + 1


def _capture_case_link(manager, draft_id, url_key, type_key, type_change=False):
    url = st.session_state[url_key]
    link_type = st.session_state.get(type_key) if type_change else None
    try:
        if draft_id:
            manager.drafts.choose_link(draft_id, url, link_type)
        else:
            st.session_state["_case_pending_link_choice"] = {"url": url, "link_type": link_type}
    except (CaseLinkError, DraftStorageError) as exc:
        st.session_state["_case_link_error"] = str(exc)
        st.session_state.pop(url_key, None)
        st.session_state.pop(type_key, None)
    if not type_change:
        st.session_state.pop(type_key, None)


def _render_links(manager, draft, description=None):
    links = draft.links if draft else extract_links(description)
    urls = [link.url for link in links]
    pending = st.session_state.get("_case_pending_link_choice")
    if not draft and pending and pending["url"] is not None and pending["url"] not in urls:
        st.session_state.pop("_case_pending_link_choice", None)
        pending = None
    if error := st.session_state.pop("_case_link_error", None):
        st.error(error)
    if not links:
        return
    selected = draft.selected_link_url if draft else pending["url"] if pending else urls[0]
    signature = sha256("\n".join(urls).encode()).hexdigest()[:12]
    scope = draft.draft_id if draft else "new"
    url_key, type_key = f"create-link:{scope}:{signature}", f"create-link-type:{scope}:{signature}"
    if url_key not in st.session_state:
        st.session_state[url_key] = selected if selected in urls else None
    picker, kind = st.columns([3, 1])
    url = picker.selectbox("Optional link", [None, *urls], format_func=lambda value: value or "No link",
                           key=url_key, on_change=_capture_case_link,
                           args=(manager, draft.draft_id if draft else None, url_key, type_key))
    link = next((item for item in links if item.url == url), None)
    if link:
        if type_key not in st.session_state:
            st.session_state[type_key] = (pending.get("link_type") if not draft and pending
                                           and pending["url"] == url else None) or link.link_type
        link_type = kind.radio("Link type", ["WEB", "VIDEO"], horizontal=True,
                               format_func=lambda value: "Website" if value == "WEB" else "Video", key=type_key,
                               on_change=_capture_case_link,
                               args=(manager, draft.draft_id if draft else None, url_key, type_key, True))
        st.caption("Slide label: " + ("WATCH VIDEO HERE." if link_type == "VIDEO" else "READ MORE HERE."))


def _render_images(manager, draft):
    pending = st.session_state.get("_case_pending_images", [])
    images = draft.images if draft else [image.metadata for image in pending]
    scope = draft.draft_id if draft else "new"
    epoch = st.session_state.get("_case_image_epoch", 0)
    if error := st.session_state.pop("_case_image_error", None):
        st.error(error)
    uploads = st.file_uploader("Upload Image", type=["png", "jpg", "jpeg"], accept_multiple_files=True,
                               max_upload_size=10, key=f"create-images:{scope}:{epoch}", disabled=len(images) >= 4,
                               help="Optional: up to four images, in the numbered order below. PNG/JPEG, up to 10 MB each.")
    if uploads:
        try:
            prepared = prepare_images([(upload.name, upload.getvalue()) for upload in uploads], existing_count=len(images))
            if draft:
                manager.drafts.add_images(draft.draft_id, prepared)
            else:
                st.session_state["_case_pending_images"] = [*pending, *prepared]
        except (CaseImageError, DraftStorageError) as exc:
            st.session_state["_case_image_error"] = str(exc)
        st.session_state["_case_image_epoch"] = epoch + 1
        st.rerun()
    if not images:
        return
    columns = st.columns(4)
    for index, image in enumerate(images):
        with columns[index]:
            try:
                preview = (manager.drafts.image_path(draft.draft_id, image, preview=True).read_bytes()
                           if draft else pending[index].preview)
            except OSError:
                st.caption("Preview unavailable. Remove and upload this image again.")
            else:
                # Small inline previews survive navigation without temporary media URLs.
                st.image("data:image/png;base64," + b64encode(preview).decode("ascii"))
            st.caption(f"Image {index + 1}: {image.filename}")
            if st.button("×", key=f"create-image-remove:{scope}:{index}", help="Remove this image"):
                if draft:
                    manager.drafts.remove_image(draft.draft_id, index)
                else:
                    st.session_state["_case_pending_images"] = [value for number, value in enumerate(pending) if number != index]
                st.rerun()
    st.caption("Images fill positions 1–4 in this order, centred and cropped to fit. Empty positions stay blank.")


def _render_history(manager, owner, current):
    if st.session_state.pop("_close_case_history", False):
        st.session_state["case-history"] = False
    popover = st.popover("History", icon=":material/history:", help="Saved case drafts",
                         key="case-history", on_change="rerun")
    if not popover.open:
        return
    entries = manager.drafts.history()
    active_ids = manager.active_case_drafts(owner)
    with popover:
        st.caption(f"Saved drafts ({len(entries)})")
        if not entries:
            st.caption("No saved drafts yet.")
            return
        with st.container(height=min(330, 128 * len(entries)), width=440, border=False):
            for entry in entries:
                draft_id = entry["draft_id"]
                with st.container(border=True, key=f"history-row-{draft_id}"):
                    opened, deleted = st.columns([8, 1], vertical_alignment="top")
                    if opened.button(entry["title"], key=f"history-open:{draft_id}", type="tertiary", width="stretch",
                                     help="Save current field edits and open this draft."):
                        editor = st.session_state.get("_case_editor")
                        if current and editor and editor["draft_id"] == current.draft_id and editor["dirty"]:
                            _save_editor(manager, current, editor)
                        manager.drafts.open(draft_id)
                        _clear_case_buffers()
                        st.session_state["_case_notice"] = "Saved draft opened."
                        st.session_state["_close_case_history"] = True
                        st.rerun()
                    opened.caption(f"{entry['client']} · {entry['updated_at'][:19].replace('T', ' ')} UTC")
                    if deleted.button("×", key=f"history-delete:{draft_id}", type="tertiary", disabled=draft_id in active_ids,
                                      help="Delete this draft, attached images and generated PPTX files. Processing drafts cannot be deleted."):
                        manager.delete_case_draft(draft_id)
                        if current and current.draft_id == draft_id:
                            _clear_case_buffers()
                        st.session_state["_case_notice"] = "Draft and its generated files deleted."
                        st.rerun()
        st.caption("Click a title to open. × permanently deletes the draft, its images and local generated files.")


def _render(manager, owner):
    draft = manager.drafts.current()
    title, history, reset = st.columns([4, 1, 1.2])
    with title:
        st.subheader("Create case")
    with history:
        _render_history(manager, owner, draft)
    with reset:
        if st.button("Start new case", key="create-new"):
            manager.drafts.start_new()
            _clear_case_buffers()
            st.rerun()
    st.caption("Draft an English case from your project notes. Missing facts stay blank; add up to four images if needed.")
    if notice := st.session_state.pop("_case_notice", None):
        st.success(notice)
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
        st.session_state["_create_description"] = description
        if draft and not busy and description.strip() != draft.original_description:
            draft = manager.drafts.update_description(draft.draft_id, description.strip())
        _render_images(manager, draft)
        _render_links(manager, draft, description)
        if st.button("Create draft", key="create-submit", type="primary", disabled=busy or not settings.api_key):
            if not description.strip():
                st.warning("Describe your project before creating a draft.")
                return
            if draft:
                draft = manager.drafts.update_description(draft.draft_id, description.strip())
            else:
                draft = manager.drafts.create(description.strip(), st.session_state.get("_case_pending_images", []))
                st.session_state.pop("_case_pending_images", None)
                if choice := st.session_state.pop("_case_pending_link_choice", None):
                    draft = manager.drafts.choose_link(draft.draft_id, choice["url"], choice["link_type"])
            manager.submit_case_generation(draft.draft_id, owner=owner)
            st.rerun()
        if busy:
            st.info("Writing your draft in the background. You can use the other tabs while it runs.")
        return

    editor = _editor(draft)
    content = Content(**editor["values"])
    visible_draft = draft.model_copy(update={"content": content,
                                            "details_skipped": draft.details_skipped if content == draft.content else False})
    with st.expander("Your project notes"):
        st.text(draft.original_description)
        if len(draft.follow_ups) > 5:
            st.caption(f"Showing the latest 5 of {len(draft.follow_ups)} additions. All additions are saved with the draft.")
        for message in draft.follow_ups[-5:]:
            st.text(message)
    _render_images(manager, draft)
    _render_links(manager, draft)
    if visible_draft.clarification_question:
        with st.chat_message("assistant"):
            st.write(visible_draft.clarification_question)
    else:
        st.caption("Add details or wording refinements whenever you need them.")
    reply_key = f"create_reply:{draft.draft_id}"
    reply_round = (draft.draft_id, draft.request_count)
    if st.session_state.get("_create_reply_round", reply_round) != reply_round:
        st.session_state[reply_key] = ""
        st.session_state["_create_reply"] = ""
    st.session_state["_create_reply_round"] = reply_round
    if reply_key not in st.session_state:
        st.session_state[reply_key] = st.session_state.get("_create_reply", "")
    reply = st.text_area("Additional details", key=reply_key, height=110, max_chars=6000, disabled=busy,
                         placeholder="Add project facts, correct information or request a wording refinement.",
                         on_change=_remember_input, args=(reply_key, "_create_reply"))
    submit, skip, _ = st.columns([1, 1, 3])
    if submit.button("Submit details", key="create-reply", disabled=busy or not settings.api_key):
        if not reply.strip():
            st.warning("Enter additional details before submitting.")
            return
        editor = st.session_state.get("_case_editor")
        if editor and editor["draft_id"] == draft.draft_id and editor["dirty"]:
            _save_editor(manager, draft, editor)
        manager.submit_case_generation(draft.draft_id, reply.strip(), owner)
        st.rerun()
    if skip.button("Skip for now", key="create-skip", disabled=busy or draft.details_skipped):
        editor = st.session_state.get("_case_editor")
        if editor and editor["draft_id"] == draft.draft_id and editor["dirty"]:
            _save_editor(manager, draft, editor)
        manager.skip_case_details(draft.draft_id, owner)
        st.rerun()
    if busy:
        st.info("Updating your draft in the background. Saved manual edits will be retained.")

    st.text_input("Title", key=f"create:{draft.draft_id}:title", max_chars=160, on_change=_capture_edit,
                  args=("title", f"create:{draft.draft_id}:title"))
    if content.title:
        st.caption(f"Slide title: {content.export_title}")
    client, services, pillar = st.columns([1, 2, 1])
    for container, name in ((client, "client"), (services, "services"), (pillar, "pillar")):
        with container:
            st.text_input(name.title(), key=f"create:{draft.draft_id}:{name}", placeholder="Unknown",
                          max_chars={"client": 200, "services": 600, "pillar": 200}[name],
                          on_change=_capture_edit, args=(name, f"create:{draft.draft_id}:{name}"))
    for name in ("brief", "creative", "impact"):
        st.text_area(name.upper(), key=f"create:{draft.draft_id}:{name}", height=135, max_chars=6000, placeholder="Not provided",
                     on_change=_capture_edit, args=(name, f"create:{draft.draft_id}:{name}"))
    if editor["dirty"]:
        st.caption("Unsaved edits. Save draft or Generate PPTX to keep them.")
    st.caption("Client and Services are saved with the draft only.")
    if content.body_words > 250:
        st.warning("The text is too long for this slide. Please shorten it before exporting.")
    if len(content.export_title) > 40:
        st.warning("The title is too long for this layout. Please shorten it.")
    exporting = manager.case_task(draft.draft_id, owner, kind="CASE_EXPORT", active_only=True)
    path, stale = _download_path(manager, draft, content, bool(editor["dirty"]))
    save, retry, generate, download = st.columns(4)
    if save.button("Save draft", key="create-save"):
        _save_editor(manager, draft, editor)
        st.session_state["_case_notice"] = "Draft saved."
        st.rerun()
    if retry.button("Retry", key="create-retry", disabled=busy or bool(exporting) or not settings.api_key,
                    help="Regenerate AI-written text. Manual edits and images are kept."):
        _save_editor(manager, draft, editor)
        manager.submit_case_generation(draft.draft_id, owner=owner, retry=True)
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
    except (GenerationError, DraftStorageError, DraftChangedError, GeneratedExportError, CaseImageError, CaseLinkError) as exc:
        st.error(str(exc))
    except ValidationError:
        st.error("Check the draft field lengths. Your previous saved draft is retained.")
