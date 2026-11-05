"""Atomic local drafts, separate from the searchable case library."""
import json
from pathlib import Path
import re
import stat
from threading import RLock
from uuid import uuid4

from pydantic import ValidationError

from app.generation.models import CASE_FIELDS, Draft, now
from app.generation.images import CaseImageError, MAX_IMAGES
from app.services.case_generation import edit_case


class DraftStorageError(ValueError):
    code = "DRAFT_STORAGE_FAILED"

    def __init__(self, message="The local draft could not be read or saved. Your previous saved draft is retained."):
        super().__init__(message)


class DraftChangedError(ValueError):
    code = "DRAFT_CHANGED"

    def __init__(self):
        super().__init__("This draft has changed since the task was submitted. Use the current draft to continue.")


class DraftStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir).resolve() / "generated_cases"
        self.lock = RLock()
        self._history_cache = None

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
            if path.name == "draft.json":
                self._history_cache = None
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

    def history(self):
        """Read saved metadata once; saves and deletions invalidate this cache."""
        with self.lock:
            if self._history_cache is None:
                entries = []
                for path in self.root.glob("*/draft.json"):
                    draft = self.load(path.parent.name)
                    entries.append({"draft_id": draft.draft_id,
                                    "title": draft.content.title or draft.original_description[:60] or "Untitled draft",
                                    "client": draft.content.client or "Unknown client", "updated_at": draft.updated_at})
                self._history_cache = sorted(entries, key=lambda item: (item["updated_at"], item["draft_id"]), reverse=True)
            return [dict(entry) for entry in self._history_cache]

    def open(self, draft_id):
        with self.lock:
            draft = self.load(draft_id)
            self._write(self.root / "current.json", {"draft_id": draft_id})
            return draft

    def delete(self, draft_id):
        """Remove one managed draft and its files, keeping metadata until last."""
        with self.lock:
            folder = self.folder(draft_id)
            entries = list(folder.rglob("*"))
            # Validate every resolved target before recursive file removal, including
            # Windows junctions. Never follow links outside this managed draft.
            for path in [folder, *entries]:
                attributes = getattr(path.lstat(), "st_file_attributes", 0)
                if (path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                        or not path.resolve().is_relative_to(folder)):
                    raise DraftStorageError()
            current = self.current()
            if current and current.draft_id == draft_id:
                self.start_new()
            self._history_cache = None
            try:
                metadata = folder / "draft.json"
                for path in sorted(entries, key=lambda entry: len(entry.parts), reverse=True):
                    if path == metadata:
                        continue
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink()
                metadata.unlink()
                folder.rmdir()
            except OSError:
                raise DraftStorageError("The draft could not be fully deleted. Close its generated files and try again.") from None

    def image_path(self, draft_id, image, *, preview=False):
        suffix = "preview.png" if preview else image.extension
        return self.folder(draft_id) / "images" / f"{image.sha256}.{suffix}"

    def _save_images(self, draft_id, images):
        for image in images:
            for preview, data in ((False, image.data), (True, image.preview)):
                path = self.image_path(draft_id, image.metadata, preview=preview)
                temporary = path.with_name(f".{uuid4().hex}.tmp")
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_bytes(data)
                    temporary.replace(path)
                except OSError:
                    raise DraftStorageError() from None
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass

    def add_images(self, draft_id, images):
        with self.lock:
            draft = self.load(draft_id)
            if len(draft.images) + len(images) > MAX_IMAGES:
                raise CaseImageError("Choose up to four images in total.")
            if not images:
                return draft
            self._save_images(draft_id, images)
            result = draft.model_copy(update={"images": [*draft.images, *(image.metadata for image in images)],
                                              "revision": draft.revision + 1, "updated_at": now()}, deep=True)
            self._write(self.folder(draft_id) / "draft.json", result.model_dump())
            return result

    def remove_image(self, draft_id, index):
        with self.lock:
            draft = self.load(draft_id)
            if not 0 <= index < len(draft.images):
                raise CaseImageError("This image is no longer in the draft. Please reopen the draft.")
            images = list(draft.images)
            images.pop(index)
            # Keep immutable files for an export already queued with the old revision.
            # Deleting the draft removes all its images and old exports together.
            result = draft.model_copy(update={"images": images, "revision": draft.revision + 1,
                                              "updated_at": now()}, deep=True)
            self._write(self.folder(draft_id) / "draft.json", result.model_dump())
            return result

    def create(self, description, images=()):
        with self.lock:
            if len(images) > MAX_IMAGES:
                raise CaseImageError("Choose up to four images in total.")
            draft = Draft(original_description=description, images=[image.metadata for image in images])
            self._save_images(draft.draft_id, images)
            self._write(self.folder(draft.draft_id) / "draft.json", draft.model_dump())
            self._write(self.root / "current.json", {"draft_id": draft.draft_id})
            return draft

    def update_description(self, draft_id, description):
        with self.lock:
            draft = self.load(draft_id)
            if draft.request_count:
                raise DraftChangedError()
            if description == draft.original_description:
                return draft
            result = Draft.model_validate({**draft.model_dump(), "original_description": description,
                                           "revision": draft.revision + 1, "updated_at": now()})
            self._write(self.folder(draft_id) / "draft.json", result.model_dump())
            return result

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
                    or current.original_description != base.original_description):
                raise DraftChangedError()
            if current.revision != base.revision:
                edits = {name: getattr(current.content, name) for name in CASE_FIELDS
                         if current.provenance.get(name) and current.provenance[name].source_kind == "USER_EDITED"}
                result = edit_case(result, edits)
            result = result.model_copy(update={"draft_id": current.draft_id, "created_at": current.created_at,
                                               "images": current.images,
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
