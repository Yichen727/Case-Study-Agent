"""One prompt and a reviewable per-slide preview, inside the selection fragment."""
import streamlit as st
from pydantic import ValidationError

from app.config import get_ai_settings
from app.services.selection_editing import SelectionEditError, body_words
from app.services.task_service import TaskQueueFull
from app.ui.selection_state import selected_ids


def _remember(key, canonical):
    st.session_state[canonical] = st.session_state[key]


def _remember_preview(case_id, key):
    st.session_state["_selection_edit_buffers"][case_id] = st.session_state[key]


def _workspace_action(manager, owner, action, request_id=None, case_ids=()):
    try:
        if action == "apply":
            manager.selection_edits.apply(owner, request_id, selected_ids(st.session_state),
                st.session_state.get("selection_revision", 0),
                {case_id: st.session_state["_selection_edit_buffers"][case_id] for case_id in case_ids})
        elif action == "restore":
            manager.selection_edits.restore(case_ids, owner)
        else:
            manager.selection_edits.discard(owner)
        if action != "discard":
            st.session_state["last_export_task_id"] = None
        st.session_state["_selection_edit_buffers"] = {}
    except SelectionEditError as exc:
        st.session_state["_selection_edit_action_error"] = str(exc)
    except ValidationError:
        st.session_state["_selection_edit_action_error"] = "Check the preview paragraph lengths. Your applied text is unchanged."


def _render(manager, owner, workspace):
    ids = selected_ids(st.session_state)
    revision = st.session_state.get("selection_revision", 0)
    active = manager.selection_edit_task(owner, active_only=True)
    pending = workspace.pending
    st.subheader("Edit selected text")
    if error := st.session_state.pop("_selection_edit_action_error", None):
        st.error(error)
    st.caption("Describe the body-text changes. Use selection numbers such as case 2 or cases 1 and 3, or say all selected cases.")
    if pending and st.session_state.get("_selection_edit_seen") != pending.plan.request_id:
        st.session_state["_selection_edit_seen"] = pending.plan.request_id
        st.session_state["_selection_edit_prompt"] = ""
        st.session_state["selection-edit-prompt"] = ""
        st.session_state["_selection_edit_buffers"] = {}
    if "selection-edit-prompt" not in st.session_state:
        st.session_state["selection-edit-prompt"] = st.session_state.get("_selection_edit_prompt", "")
    if pending and pending.result.question:
        with st.chat_message("assistant"):
            st.write(pending.result.question)
    prompt = st.text_area("Editing request", key="selection-edit-prompt", height=110, max_chars=4000,
        disabled=bool(active), placeholder="Example: For all selected cases, remove the section headings and condense each body into two paragraphs at about half its current length.",
        on_change=_remember, args=("selection-edit-prompt", "_selection_edit_prompt"))
    unresolved = bool(pending and not pending.result.question)
    if st.button("Send request", key="selection-edit-send",
                 disabled=not ids or bool(active) or unresolved or not get_ai_settings().api_key):
        if not prompt.strip():
            st.warning("Describe the text changes before sending.")
        else:
            manager.submit_selection_edit(ids, prompt.strip(), revision, owner)
            st.rerun()
    if not get_ai_settings().api_key:
        st.caption("Configure the OpenAI API key to request a rewrite. Existing applied versions can still be reviewed or restored.")
    if active:
        st.info("Rewriting selected text in the background. You can use other tabs or update your selection.")
    if pending:
        stale = (pending.plan.selection_ids != list(ids) or pending.plan.selection_revision != revision
                 or pending.plan.workspace_revision != workspace.revision)
        if stale:
            st.info("The selection has changed. Discard this preview and send a new request.")
        if not pending.result.question:
            st.caption("Preview only. Check the wording and targets; you can edit each preview before applying.")
            buffers = st.session_state.setdefault("_selection_edit_buffers", {})
            slides = {slide.case_id: slide for slide in pending.plan.slides}
            supported = []
            for change in pending.result.changes:
                slide = slides[change.case_id]
                with st.container(border=True):
                    st.write(f"**{slide.selection_number}. {slide.title}**")
                    st.caption(f"{slide.source_file} · original slide {slide.slide_number}")
                    if change.issue:
                        st.info(change.issue)
                        continue
                    supported.append(change.case_id)
                    before, after = st.columns(2)
                    before.text_area("Before", value="\n\n".join(p.text for p in slide.current), height=200,
                        disabled=True, key=f"edit-before:{pending.plan.request_id}:{change.case_id}")
                    key = f"edit-preview:{pending.plan.request_id}:{change.case_id}"
                    buffers.setdefault(change.case_id, "\n\n".join(p.text for p in change.paragraphs))
                    st.session_state[key] = buffers[change.case_id]
                    after.text_area("Preview", key=key, height=200, max_chars=10000,
                                    on_change=_remember_preview, args=(change.case_id, key))
                    st.caption(f"Body words: {body_words(slide.current)} before · {body_words(change.paragraphs)} in the AI preview")
                    with st.expander("Supporting text"):
                        seen = set()
                        for paragraph in change.paragraphs:
                            for proof in paragraph.evidence:
                                marker = (proof.source, proof.text)
                                if marker not in seen:
                                    st.caption("Original slide" if proof.source == "SLIDE" else "User-provided information")
                                    st.text(proof.text)
                                    seen.add(marker)
            apply, discard, _ = st.columns([1, 1, 3])
            apply.button("Apply changes", key="selection-edit-apply", disabled=stale or not supported or bool(active),
                         on_click=_workspace_action, args=(manager, owner, "apply", pending.plan.request_id, supported))
            discard.button("Discard", key="selection-edit-discard", on_click=_workspace_action,
                           args=(manager, owner, "discard"))
        else:
            st.button("Discard request", key="selection-edit-discard-question", on_click=_workspace_action,
                      args=(manager, owner, "discard"))
    applied_ids = [case_id for case_id in ids if case_id in workspace.applied]
    if applied_ids:
        st.caption("Applied versions are stored locally. They do not change the case library or original presentations.")
        for case_id in applied_ids:
            applied = workspace.applied[case_id]
            text, restore = st.columns([4, 1])
            with text.expander(f"Edited version · {applied.slide.title} · {applied.slide.source_file} · slide {applied.slide.slide_number}"):
                st.text("\n\n".join(p.text for p in applied.paragraphs))
            restore.button("Restore original", key=f"selection-edit-restore:{case_id}", disabled=unresolved,
                           on_click=_workspace_action, args=(manager, owner, "restore", None, [case_id]))


def render_selection_edits(manager, owner, workspace=None):
    try:
        _render(manager, owner, workspace if workspace is not None else manager.selection_edits.load(owner))
    except SelectionEditError as exc:
        st.error(str(exc))
    except TaskQueueFull as exc:
        st.warning(str(exc))
    except ValidationError:
        st.error("Check the preview paragraph lengths. Your applied text is unchanged.")
