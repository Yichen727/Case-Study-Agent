"""One local edit workspace per operator, separate from cases and source snapshots."""
from hashlib import sha256
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from app.services.selection_editing import EditPlan, EditResult, Paragraph, SlideText, SelectionEditError


class AppliedEdit(BaseModel):
    slide: SlideText
    paragraphs: list[Paragraph]
    user_facts: list[str] = Field(default_factory=list)
    revision: int = 1


class Preview(BaseModel):
    plan: EditPlan
    result: EditResult


class EditWorkspace(BaseModel):
    schema_version: int = 1
    revision: int = 0
    applied: dict[str, AppliedEdit] = Field(default_factory=dict)
    pending: Preview | None = None
    active_request: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0


class SelectionEditStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / "selected_edits"
        self.lock = RLock()

    def path(self, owner):
        return self.root / f"{sha256(owner.encode()).hexdigest()[:32]}.json"

    def load(self, owner="local"):
        with self.lock:
            path = self.path(owner)
            if not path.exists():
                return EditWorkspace()
            try:
                return EditWorkspace.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, ValidationError):
                raise SelectionEditError("EDIT_STORAGE_FAILED") from None

    def _write(self, owner, workspace):
        path = self.path(owner)
        temporary = path.with_name(f".{uuid4().hex}.tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary.write_text(workspace.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            raise SelectionEditError("EDIT_STORAGE_FAILED") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def begin(self, owner, plan):
        with self.lock:
            workspace = self.load(owner)
            if workspace.revision != plan.workspace_revision:
                raise SelectionEditError("EDIT_STALE")
            workspace.active_request = plan.request_id
            self._write(owner, workspace)

    def complete(self, owner, plan, result):
        with self.lock:
            workspace = self.load(owner)
            workspace.input_tokens += result.input_tokens
            workspace.output_tokens += result.output_tokens
            workspace.elapsed_seconds += result.elapsed_seconds
            if workspace.active_request != plan.request_id or workspace.revision != plan.workspace_revision:
                self._write(owner, workspace)
                return False
            workspace.pending = Preview(plan=plan, result=result)
            workspace.active_request = None
            self._write(owner, workspace)
            return True

    def apply(self, owner, request_id, selection_ids, selection_revision, edits=None):
        with self.lock:
            workspace = self.load(owner)
            preview = workspace.pending
            if (preview is None or preview.plan.request_id != request_id or preview.result.question
                    or preview.plan.workspace_revision != workspace.revision
                    or preview.plan.selection_ids != list(selection_ids)
                    or preview.plan.selection_revision != selection_revision):
                raise SelectionEditError("EDIT_STALE")
            slides = {slide.case_id: slide for slide in preview.plan.slides}
            changes = [change for change in preview.result.changes if not change.issue]
            if not changes or set(edits or {}) - {change.case_id for change in changes}:
                raise SelectionEditError("EDIT_OUTPUT_INVALID")
            for change in changes:
                original_text = "\n\n".join(paragraph.text for paragraph in change.paragraphs)
                text = (edits or {}).get(change.case_id, original_text)
                blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
                if not 1 <= len(blocks) <= 24 or len(text) > 10000:
                    raise SelectionEditError("EDIT_OUTPUT_INVALID")
                paragraphs = [Paragraph(text=block, role=change.paragraphs[index].role
                    if len(blocks) == len(change.paragraphs) else "BODY") for index, block in enumerate(blocks)]
                slide = slides[change.case_id]
                previous = workspace.applied.get(change.case_id)
                facts = list(slide.user_facts)
                if text != original_text:
                    facts.extend(block for block in blocks if block not in [p.text for p in change.paragraphs])
                facts = list(dict.fromkeys(facts))
                while sum(map(len, facts)) > 8000:
                    facts.pop(0)
                source = slide.model_copy(update={"current": slide.original, "user_facts": []})
                workspace.applied[change.case_id] = AppliedEdit(slide=source, paragraphs=paragraphs, user_facts=facts,
                                                              revision=previous.revision + 1 if previous else 1)
            workspace.revision += 1
            workspace.pending = None
            workspace.active_request = None
            self._write(owner, workspace)
            return workspace

    def discard(self, owner="local"):
        with self.lock:
            workspace = self.load(owner)
            workspace.pending = None
            workspace.active_request = None
            workspace.revision += 1
            self._write(owner, workspace)

    def restore(self, case_ids, owner="local"):
        with self.lock:
            workspace = self.load(owner)
            for case_id in case_ids:
                workspace.applied.pop(case_id, None)
            workspace.pending = None
            workspace.active_request = None
            workspace.revision += 1
            self._write(owner, workspace)
