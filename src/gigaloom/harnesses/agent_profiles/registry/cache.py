"""Private content-addressed cache for validated ACP Registry snapshots."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Mapping, cast
from uuid import uuid4

from gigaloom.harnesses.agent_profiles.registry.errors import RegistryCacheError
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock
from gigaloom.harnesses.agent_profiles.registry.models import ACPRegistryCatalog
from gigaloom.harnesses.agent_profiles.registry.schema import (
    MAX_REGISTRY_DOCUMENT_BYTES,
    OFFICIAL_ACP_REGISTRY_URL,
    decode_registry_document,
)


REGISTRY_CACHE_SCHEMA_VERSION = 1
DEFAULT_REGISTRY_STALE_AFTER = timedelta(hours=24)
MAX_CACHE_POINTER_BYTES = 16 * 1024


class ACPRegistryCache:
    """Persist only the last valid pointer plus immutable raw snapshot blobs."""

    def __init__(
        self,
        root: str | Path,
        *,
        stale_after: timedelta = DEFAULT_REGISTRY_STALE_AFTER,
    ) -> None:
        self.root = Path(root)
        if stale_after <= timedelta(0) or stale_after > timedelta(days=30):
            raise ValueError("ACP registry stale_after is outside the allowed range")
        self.stale_after = stale_after

    @property
    def current_path(self) -> Path:
        """Return the atomic pointer path for diagnostics and tests."""
        return self.root / "current.json"

    @property
    def snapshots_root(self) -> Path:
        """Return the immutable content-addressed blob directory."""
        return self.root / "snapshots"

    def load(self, *, now: datetime) -> ACPRegistryCatalog | None:
        """Load and fully revalidate the current cached revision, if present."""
        _validate_timestamp(now, field_name="cache read time")
        self._validate_existing_root()
        if not self.current_path.exists():
            return None
        with registry_cache_lock(self.root / ".cache.lock"):
            if not self.current_path.exists():
                return None
            pointer = self._read_pointer()
            blob = self._read_blob(
                _string(pointer["snapshot_digest"], "snapshot_digest")
            )
        fetched_at = _timestamp(pointer["fetched_at"], "fetched_at")
        stale = now - fetched_at > self.stale_after
        catalog = decode_registry_document(
            blob,
            fetched_at=fetched_at,
            source_url=_string(pointer["source_url"], "source_url"),
            etag=_optional_string(pointer["etag"], "etag"),
            last_modified=_optional_string(
                pointer["last_modified"],
                "last_modified",
            ),
            stale=stale,
        )
        self._validate_pointer(pointer, catalog)
        return catalog.as_cached(stale=stale)

    def store(self, payload: bytes, catalog: ACPRegistryCatalog) -> None:
        """Publish one already validated snapshot without replacing immutable bytes."""
        if hashlib.sha256(payload).hexdigest() != catalog.snapshot.snapshot_digest:
            raise RegistryCacheError("ACP registry payload digest mismatch")
        if catalog.snapshot.source_url != OFFICIAL_ACP_REGISTRY_URL:
            raise RegistryCacheError("ACP registry cache source is not canonical")
        if catalog.offline or catalog.snapshot.stale:
            raise RegistryCacheError("stale or offline registry state cannot be stored")
        self._ensure_roots()
        with registry_cache_lock(self.root / ".cache.lock"):
            self._write_blob(catalog.snapshot.snapshot_digest, payload)
            self._write_pointer(_pointer_for(catalog))

    def mark_revalidated(
        self,
        catalog: ACPRegistryCatalog,
        *,
        fetched_at: datetime,
        etag: str | None,
        last_modified: str | None,
    ) -> ACPRegistryCatalog:
        """Advance freshness metadata after an exact conditional 304 response."""
        _validate_timestamp(fetched_at, field_name="cache revalidation time")
        snapshot = replace(
            catalog.snapshot,
            fetched_at=fetched_at,
            etag=etag,
            last_modified=last_modified,
            stale=False,
        )
        updated = replace(
            catalog,
            snapshot=snapshot,
            offline=False,
            from_cache=True,
            refresh_error_code=None,
        )
        self._ensure_roots()
        with registry_cache_lock(self.root / ".cache.lock"):
            self._read_blob(snapshot.snapshot_digest)
            self._write_pointer(_pointer_for(updated))
        return updated

    def _ensure_roots(self) -> None:
        for path in (self.root, self.snapshots_root):
            if path.exists() and path.is_symlink():
                raise RegistryCacheError("ACP registry cache path cannot be a symlink")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            mode = path.lstat().st_mode
            if not stat.S_ISDIR(mode):
                raise RegistryCacheError("ACP registry cache path must be a directory")

    def _validate_existing_root(self) -> None:
        if not self.root.exists():
            if self.root.is_symlink():
                raise RegistryCacheError("ACP registry cache path cannot be a symlink")
            return
        if self.root.is_symlink() or not self.root.is_dir():
            raise RegistryCacheError(
                "ACP registry cache path must be a non-symlink directory"
            )

    def _read_pointer(self) -> Mapping[str, object]:
        raw = _read_regular_file(self.current_path, maximum=MAX_CACHE_POINTER_BYTES)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RegistryCacheError(
                "ACP registry cache pointer is invalid JSON"
            ) from error
        if not isinstance(value, dict):
            raise RegistryCacheError("ACP registry cache pointer must be an object")
        expected = {
            "schema_version",
            "source_url",
            "registry_version",
            "fetched_at",
            "etag",
            "last_modified",
            "snapshot_digest",
            "entry_count",
            "entries_digest",
        }
        if set(value) != expected:
            raise RegistryCacheError("ACP registry cache pointer fields are invalid")
        if value["schema_version"] != REGISTRY_CACHE_SCHEMA_VERSION:
            raise RegistryCacheError("ACP registry cache schema version is unsupported")
        return cast(Mapping[str, object], value)

    def _read_blob(self, digest: str) -> bytes:
        if not _is_digest(digest):
            raise RegistryCacheError("ACP registry cache digest is invalid")
        path = self.snapshots_root / f"{digest}.json"
        raw = _read_regular_file(path, maximum=MAX_REGISTRY_DOCUMENT_BYTES)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RegistryCacheError("ACP registry cached snapshot digest mismatch")
        return raw

    def _write_blob(self, digest: str, payload: bytes) -> None:
        path = self.snapshots_root / f"{digest}.json"
        if path.exists():
            current = _read_regular_file(path, maximum=MAX_REGISTRY_DOCUMENT_BYTES)
            if current != payload:
                raise RegistryCacheError("immutable ACP registry cache blob differs")
            return
        _write_exclusive(path, payload)

    def _write_pointer(self, pointer: Mapping[str, object]) -> None:
        raw = (
            json.dumps(
                pointer,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()
        _write_atomic(self.current_path, raw)

    @staticmethod
    def _validate_pointer(
        pointer: Mapping[str, object],
        catalog: ACPRegistryCatalog,
    ) -> None:
        expected = _pointer_for(catalog)
        if dict(pointer) != expected:
            raise RegistryCacheError(
                "ACP registry cache pointer does not match its blob"
            )


def _pointer_for(catalog: ACPRegistryCatalog) -> dict[str, object]:
    snapshot = catalog.snapshot
    return {
        "schema_version": REGISTRY_CACHE_SCHEMA_VERSION,
        "source_url": snapshot.source_url,
        "registry_version": catalog.registry_version,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "etag": snapshot.etag,
        "last_modified": snapshot.last_modified,
        "snapshot_digest": snapshot.snapshot_digest,
        "entry_count": snapshot.entry_count,
        "entries_digest": snapshot.entries_digest,
    }


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RegistryCacheError(
            f"ACP registry cache file is unavailable: {path.name}"
        ) from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise RegistryCacheError("ACP registry cache file must be a regular file")
        if metadata.st_size <= 0 or metadata.st_size > maximum:
            raise RegistryCacheError("ACP registry cache file size is invalid")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            payload = stream.read(maximum + 1)
        if not payload or len(payload) > maximum:
            raise RegistryCacheError("ACP registry cache file size is invalid")
        return payload
    except RegistryCacheError:
        raise
    except OSError as error:
        raise RegistryCacheError(
            f"ACP registry cache file is unreadable: {path.name}"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        current = _read_regular_file(path, maximum=MAX_REGISTRY_DOCUMENT_BYTES)
        if current != payload:
            raise RegistryCacheError("immutable ACP registry cache blob differs")
        return
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        _write_exclusive(temporary, payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_timestamp(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RegistryCacheError(f"ACP registry cache {field_name} is invalid")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise RegistryCacheError(
            f"ACP registry cache {field_name} is invalid"
        ) from error
    if result.tzinfo is None:
        raise RegistryCacheError(f"ACP registry cache {field_name} is invalid")
    return result


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RegistryCacheError(f"ACP registry cache {field_name} is invalid")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
