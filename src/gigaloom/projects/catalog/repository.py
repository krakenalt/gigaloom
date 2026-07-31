"""Recoverable authoritative filesystem project catalog repository."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Iterator

from .codec import decode_catalog_entry, encode_catalog_entry
from .errors import (
    ProjectCatalogCapacityError,
    ProjectCatalogConflictError,
    ProjectCatalogNotFoundError,
)
from .models import (
    MAX_CATALOG_ENTRIES,
    MAX_CATALOG_PAGE_SIZE,
    ProjectCatalogEntryV1,
    ProjectCatalogPageV1,
)

_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


class FilesystemProjectCatalogRepository:
    """Persist one canonical JSON file per authoritative catalog entry."""

    def __init__(self, catalog_dir: str | Path) -> None:
        self.catalog_dir = Path(catalog_dir).expanduser()
        self.entries_dir = self.catalog_dir / "entries"
        self.lock_path = self.catalog_dir / "catalog"

    def create(self, entry: ProjectCatalogEntryV1) -> ProjectCatalogEntryV1:
        """Create a new entry, rejecting identity and location collisions."""
        with _exclusive_file_lock(self.lock_path):
            existing = self._read_all()
            if any(
                item.catalog_project_id == entry.catalog_project_id for item in existing
            ):
                raise ProjectCatalogConflictError(
                    f"catalog project already exists: {entry.catalog_project_id}"
                )
            if len(existing) >= MAX_CATALOG_ENTRIES:
                raise ProjectCatalogCapacityError("project catalog capacity exceeded")
            self._assert_unique(entry, existing)
            self._write(entry)
        return entry

    def get(self, catalog_project_id: str) -> ProjectCatalogEntryV1:
        """Return one catalog entry by stable id."""
        path = self._entry_path(catalog_project_id)
        try:
            return decode_catalog_entry(path.read_bytes())
        except FileNotFoundError as exc:
            raise ProjectCatalogNotFoundError(catalog_project_id) from exc

    def replace(
        self,
        entry: ProjectCatalogEntryV1,
        *,
        expected_revision: int,
    ) -> ProjectCatalogEntryV1:
        """Replace one entry using optimistic revision control."""
        with _exclusive_file_lock(self.lock_path):
            current = self.get(entry.catalog_project_id)
            if current.revision != expected_revision:
                raise ProjectCatalogConflictError("project catalog revision is stale")
            if entry.revision != expected_revision + 1:
                raise ProjectCatalogConflictError(
                    "project catalog revision must advance exactly once"
                )
            if entry.created_at != current.created_at:
                raise ProjectCatalogConflictError("project creation time is immutable")
            others = tuple(
                item
                for item in self._read_all()
                if item.catalog_project_id != entry.catalog_project_id
            )
            self._assert_unique(entry, others)
            self._write(entry)
        return entry

    def list_page(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        include_tombstoned: bool = False,
    ) -> ProjectCatalogPageV1:
        """Return a stable id-ordered page from a bounded catalog scan."""
        if not 1 <= limit <= MAX_CATALOG_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_CATALOG_PAGE_SIZE}")
        entries = tuple(
            entry
            for entry in self._read_all()
            if (include_tombstoned or entry.state != "tombstoned")
            and (cursor is None or entry.catalog_project_id > cursor)
        )
        selected = entries[: limit + 1]
        has_more = len(selected) > limit
        items = selected[:limit]
        return ProjectCatalogPageV1(
            items=items,
            next_cursor=items[-1].catalog_project_id if has_more and items else None,
            has_more=has_more,
        )

    def entries_for_migration(self) -> tuple[ProjectCatalogEntryV1, ...]:
        """Return the bounded authoritative set for an explicit migration."""
        return self._read_all()

    def _read_all(self) -> tuple[ProjectCatalogEntryV1, ...]:
        if not self.entries_dir.exists():
            return ()
        paths = sorted(self.entries_dir.glob("prj_*.json"))
        if len(paths) > MAX_CATALOG_ENTRIES:
            raise ProjectCatalogCapacityError("project catalog capacity exceeded")
        return tuple(decode_catalog_entry(path.read_bytes()) for path in paths)

    def _assert_unique(
        self,
        candidate: ProjectCatalogEntryV1,
        existing: tuple[ProjectCatalogEntryV1, ...],
    ) -> None:
        candidate_location = _location_key(candidate)
        for entry in existing:
            if entry.harness_project_id == candidate.harness_project_id:
                raise ProjectCatalogConflictError(
                    "harness project identity is already cataloged"
                )
            if (
                candidate_location is not None
                and _location_key(entry) == candidate_location
            ):
                raise ProjectCatalogConflictError(
                    "project location is already cataloged"
                )

    def _entry_path(self, catalog_project_id: str) -> Path:
        if (
            not catalog_project_id.startswith("prj_")
            or len(catalog_project_id) != 28
            or not catalog_project_id[4:].isalnum()
        ):
            raise ValueError("invalid catalog_project_id")
        return self.entries_dir / f"{catalog_project_id}.json"

    def _write(self, entry: ProjectCatalogEntryV1) -> None:
        path = self._entry_path(entry.catalog_project_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encode_catalog_entry(entry))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            _fsync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _location_key(entry: ProjectCatalogEntryV1) -> str | None:
    value = entry.location.canonical_path
    return os.path.normcase(value).casefold() if value is not None else None


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(lock_path.resolve())
    with _LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_descriptor(descriptor)
            yield
        finally:
            _unlock_descriptor(descriptor)
            os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
