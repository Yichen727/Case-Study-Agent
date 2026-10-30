"""Immutable PPTX snapshots, source duplicate lookup, and reference protection."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from app.db.repository import connect
from app.models import SourceRecord
from app.ingestion.errors import IngestionError
from app.ingestion.validation import MAX_FILE_BYTES

_snapshot_lock = RLock()
_leases: dict[str, int] = {}


def find_local_import(db_path, source):
    """Reuse identical local uploads without merging independent legacy sources."""
    if source.source_type != "LOCAL":
        return None
    with connect(db_path) as conn:
        row = conn.execute("""SELECT src.*,
                              (SELECT COUNT(*) FROM cases c WHERE c.source_id=src.source_id) AS case_count
                              FROM sources src WHERE source_type='LOCAL' AND content_sha256=?
                              AND ingestion_status='PARSED' AND requires_reimport=0
                              AND source_snapshot_reference=? ORDER BY imported_at,source_id LIMIT 1""",
                           (source.content_sha256,source.source_snapshot_reference)).fetchone()
        return dict(row) if row else None


def _references(db_path: Path) -> set[str] | None:
    references = set()
    databases = [db_path, *sorted((db_path.parent / "backups").glob("*.sqlite3"))]
    try:
        for database in databases:
            if not database.is_file() or database.is_symlink():
                continue
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchone():
                    references.update(str(Path(row[0]).resolve()) for row in conn.execute(
                        "SELECT source_snapshot_reference FROM sources WHERE source_snapshot_reference IS NOT NULL"))
        return references
    except sqlite3.Error:
        return None  # An unreadable backup is not permission to delete its snapshot.


def snapshot_local_source(
    pptx_path: str | Path, db_path: str | Path, filename: str, *,
    source_type: str = "LOCAL", original_source_url: str | None = None,
    source_file_id: str | None = None, source_version: str | None = None,
    source_id: str | None = None,
) -> SourceRecord:
    source_id = source_id or uuid4().hex
    database = Path(db_path).resolve()
    folder = database.parent / "uploads"
    staged = folder / f"{uuid4().hex}.pending"
    digest = hashlib.sha256()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with Path(pptx_path).open("rb") as original, staged.open("xb") as snapshot:
            size = 0
            while chunk := original.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise IngestionError("INVALID_FILE")
                digest.update(chunk)
                snapshot.write(chunk)
        checksum = digest.hexdigest()
        with _snapshot_lock:
            destination = folder / f"{checksum}.pptx"
            # Reuse existing UUID snapshots too; no destructive relocation of old sources.
            if not destination.exists() and database.exists():
                with closing(sqlite3.connect(database)) as conn:
                    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sources'").fetchone():
                        row = conn.execute(
                            "SELECT source_snapshot_reference FROM sources WHERE content_sha256=? AND requires_reimport=0 LIMIT 1",
                            (checksum,)).fetchone()
                        if row and row[0]:
                            previous = Path(row[0]).resolve()
                            if previous.parent == folder and previous.is_file() and not previous.is_symlink():
                                destination = previous
            if destination.exists():
                staged.unlink()
            else:
                staged.replace(destination)
            key = str(destination)
            _leases[key] = _leases.get(key, 0) + 1
    except IngestionError:
        staged.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        staged.unlink(missing_ok=True)
        raise IngestionError("FETCH_FAILED") from exc
    return SourceRecord(
        source_id=source_id, filename=filename,
        source_snapshot_reference=str(destination), content_sha256=checksum,
        imported_at=datetime.now(timezone.utc).isoformat(),
        source_type=source_type, original_source_url=original_source_url,
        source_file_id=source_file_id, source_version=source_version,
    )


def release_snapshot(source: SourceRecord, db_path: str | Path, committed: bool) -> None:
    path = Path(source.source_snapshot_reference).resolve()
    with _snapshot_lock:
        remaining = _leases.get(str(path), 1) - 1
        if remaining:
            _leases[str(path)] = remaining
        else:
            _leases.pop(str(path), None)
        if not committed and not remaining:
            references = _references(Path(db_path).resolve())
            if references is not None and str(path) not in references:
                path.unlink(missing_ok=True)


def unused_snapshots(db_path: str | Path) -> list[Path]:
    database = Path(db_path).resolve()
    folder = database.parent / "uploads"
    with _snapshot_lock:
        references = _references(database)
        if references is None:
            raise RuntimeError("Snapshot references could not be checked. Please try again later.")
        return [path for path in sorted(folder.glob("*.pptx"))
                if not path.is_symlink() and path.resolve().parent == folder
                and str(path.resolve()) not in references and str(path.resolve()) not in _leases]


def remove_unused_snapshots(db_path: str | Path) -> int:
    with _snapshot_lock:
        candidates = unused_snapshots(db_path)  # Recheck at deletion time, including backups and active imports.
        for path in candidates:
            if path.resolve().parent != Path(db_path).resolve().parent / "uploads":
                raise ValueError("Snapshot path leaves the managed folder.")
            path.unlink()
        return len(candidates)
