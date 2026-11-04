"""Atomic local drafts, separate from the searchable case library."""
import json
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from pydantic import ValidationError

from app.generation.models import CASE_FIELDS, Draft, now
from app.services.case_generation import edit_case


class DraftStorageError(ValueError):
    code = "DRAFT_STORAGE_FAILED"

    def __init__(self):
        super().__init__("The local draft could not be read or saved. Your previous saved draft is retained.")


class DraftChangedError(ValueError):
    code = "DRAFT_CHANGED"

    def __init__(self):
        super().__init__("This draft has changed since the task was submitted. Use the current draft to continue.")


class DraftStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / "generated_cases"
        self.lock = RLock()

    def folder(self, draft_id):
        if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
            raise DraftStorageError()
        path = self.root / draft_id
        if path.resolve().parent != self.root:
            raise DraftStorageError()
        return path

    def _read(self, path):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise DraftStorageError()
            return value
        except (OSError, ValueError):
            raise DraftStorageError() from None

    def _write(self, path, value):
        temporary = path.with_name(f".{path.name}-{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            raise DraftStorageError() from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, draft_id):
        with self.lock:
            try:
                draft = Draft.model_validate(self._read(self.folder(draft_id) / "draft.json"))
                if draft.draft_id != draft_id:
                    raise DraftStorageError()
                return draft
            except ValidationError:
                raise DraftStorageError() from None

    def current(self):
        with self.lock:
            pointer = self.root / "current.json"
            if not pointer.exists():
                return None
            draft_id = self._read(pointer).get("draft_id")
            return self.load(draft_id) if draft_id else None

    def start_new(self):
        """Leave previous drafts intact; the next submitted input creates a new ID."""
        with self.lock:
            self._write(self.root / "current.json", {"draft_id": None})

    def create(self, description):
        with self.lock:
            draft = Draft(original_description=description)
            self._write(self.folder(draft.draft_id) / "draft.json", draft.model_dump())
            self._write(self.root / "current.json", {"draft_id": draft.draft_id})
            return draft

    def edit(self, draft_id, changes):
        with self.lock:
            current = self.load(draft_id)
            edited = edit_case(current, changes)
            if edited.content == current.content:
                return current
            self._write(self.folder(draft_id) / "draft.json", edited.model_dump())
            return edited

    def commit_generated(self, base, result):
        """Merge newer manual edits, but never replay an already completed AI step."""
        with self.lock:
            current = self.load(base.draft_id)
            if (current.request_count != base.request_count
                    or current.clarification_used != base.clarification_used
                    or current.original_description != base.original_description):
                raise DraftChangedError()
            if current.revision != base.revision:
                edits = {name: getattr(current.content, name) for name in CASE_FIELDS
                         if current.provenance.get(name) and current.provenance[name].source_kind == "USER_EDITED"}
                result = edit_case(result, edits)
            result = result.model_copy(update={"draft_id": current.draft_id, "created_at": current.created_at,
                                               "revision": current.revision + 1, "updated_at": now()}, deep=True)
            self._write(self.folder(base.draft_id) / "draft.json", result.model_dump())
            return result

    def last_export(self, draft_id):
        with self.lock:
            path = self.folder(draft_id) / "export.json"
            return self._read(path) if path.exists() else None

    def record_export(self, draft_id, result):
        with self.lock:
            self._write(self.folder(draft_id) / "export.json", result)
