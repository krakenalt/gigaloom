# ruff: noqa: E402, F401, F403, F405
"""Durable integration catalog and offline MCP subregistry contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import anyio

from gpt2giga_harness.integration_packages import (
    IntegrationPackage,
    IntegrationSourceType,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_from_dict,
    integration_package_semantic_hash,
    integration_package_to_dict,
)
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock
from gpt2giga_harness.types import REDACTED, redact_secrets


CATALOG_SCHEMA_VERSION = 1
OFFICIAL_MCP_REGISTRY_SOURCE_ID = "official-mcp-registry"
OFFICIAL_MCP_REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
OFFICIAL_MCP_REGISTRY_API_VERSION = "v0.1"
MAX_CATALOG_ENTRIES = 50_000
MAX_CATALOG_SOURCE_ENTRIES = 10_000
MAX_CATALOG_SOURCES = 100
MAX_CATALOG_PAGE_SIZE = 1_000
MAX_CATALOG_SOURCE_ERRORS = 20
MAX_CATALOG_JSON_BYTES = 256 * 1024
MAX_CATALOG_JSON_DEPTH = 20
MAX_REGISTRY_PAGES = 1_000
MAX_REGISTRY_RESPONSE_BYTES = 2 * 1024 * 1024
_MCP_OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"
_LOCAL_SUBREGISTRY_META_KEY = "agent_workbench.catalog/v1"
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_MCP_NAME_RE = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IMMUTABLE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,511}\Z")
from .codec import *  # noqa: F403
from .models import *  # noqa: F403
from .validation import *  # noqa: F403


class IntegrationCatalogStore:
    """Atomically persist immutable catalog pins and source health."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(data_dir).expanduser().resolve() / "integrations"
        self.path = self.root / "catalog.json"
        self.lock_path = self.root / ".catalog.json.lock"
        self._now = now or (lambda: datetime.now(timezone.utc))

    def snapshot(self) -> CatalogSnapshot:
        """Load the last complete local snapshot without network access."""
        self._ensure_root()
        with exclusive_file_lock(self.path):
            return self._read_unlocked()

    def list(self) -> tuple[CatalogEntry, ...]:
        """Return all cached catalog entries in deterministic order."""
        return self.snapshot().entries

    def get(self, catalog_id: str) -> CatalogEntry | None:
        """Return one catalog entry by stable local id."""
        _validate_identity(catalog_id, field_name="catalog id")
        return next(
            (item for item in self.snapshot().entries if item.catalog_id == catalog_id),
            None,
        )

    def delete_definition(
        self,
        catalog_id: str,
        *,
        expected_revision: int,
    ) -> CatalogEntry:
        """Delete one exact user-owned catalog definition after dependency checks."""
        _validate_identity(catalog_id, field_name="catalog id")
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValueError("catalog expected revision is invalid")
        self._ensure_root()
        with exclusive_file_lock(self.path):
            snapshot = self._read_unlocked()
            if snapshot.revision != expected_revision:
                raise CatalogConflictError(
                    "catalog changed after definition deletion preview"
                )
            entry = next(
                (item for item in snapshot.entries if item.catalog_id == catalog_id),
                None,
            )
            if entry is None:
                raise CatalogConflictError("catalog definition was not found")
            if entry.source_type not in {
                CatalogSourceType.GIT,
                CatalogSourceType.LOCAL,
            }:
                raise CatalogConflictError(
                    "only user-owned Git or local definitions can be deleted"
                )
            remaining = tuple(
                item for item in snapshot.entries if item.catalog_id != catalog_id
            )
            sources = tuple(
                replace(
                    source,
                    entry_count=sum(
                        item.source_id == source.source_id for item in remaining
                    ),
                )
                if source.source_id == entry.source_id
                else source
                for source in snapshot.sources
            )
            self._write_unlocked(
                CatalogSnapshot(
                    revision=snapshot.revision + 1,
                    updated_at=_format_timestamp(self._now()),
                    entries=remaining,
                    sources=sources,
                )
            )
            return entry

    def import_package(
        self,
        package: IntegrationPackage,
        *,
        source_id: str,
        source_type: CatalogSourceType,
        federated: FederatedCatalogMetadata | None = None,
    ) -> CatalogEntry:
        """Import one reviewed immutable manifest without reading its source."""
        if not isinstance(package, IntegrationPackage):
            raise TypeError("catalog import requires an IntegrationPackage")
        _validate_import_source(package.source_type, source_type)
        timestamp = _format_timestamp(self._now())
        entry = CatalogEntry(
            catalog_id=_catalog_id(source_id, package.id, package.version),
            source_id=source_id,
            source_type=source_type,
            package_id=package.id,
            version=package.version,
            immutable_ref=package.immutable_ref,
            content_hash=integration_package_semantic_hash(package),
            status=CatalogEntryStatus.ACTIVE,
            pinned=True,
            source_present=True,
            install_authorized=False,
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            package=package,
            federated=federated,
        )
        result = self._merge_source(
            source_id=source_id,
            source_type=source_type,
            incoming=(entry,),
            observed_at=timestamp,
            complete=False,
            raise_on_conflict=True,
        )
        stored = self.get(entry.catalog_id)
        if stored is None:  # pragma: no cover - guarded by the merge contract
            raise CatalogStateError("catalog import was not persisted")
        if not result.success:  # pragma: no cover - conflicts raise above
            raise CatalogConflictError("catalog import conflicted")
        return stored

    def import_manifest(
        self,
        payload: Mapping[str, Any],
        *,
        source_id: str,
        source_type: CatalogSourceType,
    ) -> CatalogEntry:
        """Parse and import one strict N4 IntegrationPackage manifest."""
        return self.import_package(
            integration_package_from_dict(payload),
            source_id=source_id,
            source_type=source_type,
        )

    def _merge_source(
        self,
        *,
        source_id: str,
        source_type: CatalogSourceType,
        incoming: Sequence[CatalogEntry],
        observed_at: str,
        complete: bool,
        raise_on_conflict: bool = False,
        fail_entire_source_on_conflict: bool = False,
        cursor: str | None = None,
        retry_count: int = 0,
        next_retry_at: str | None = None,
        etag: str | None = None,
        freshness_expires_at: str | None = None,
    ) -> CatalogSyncResult:
        _validate_identity(source_id, field_name="catalog source id")
        if not isinstance(source_type, CatalogSourceType):
            raise ValueError("catalog source type is invalid")
        incoming_by_id = {item.catalog_id: item for item in incoming}
        if len(incoming_by_id) != len(incoming):
            raise ValueError("catalog source returned duplicate entries")
        if len(incoming_by_id) > MAX_CATALOG_SOURCE_ENTRIES:
            raise ValueError("catalog source returned too many entries")
        self._ensure_root()
        with exclusive_file_lock(self.path):
            snapshot = self._read_unlocked()
            entries = {item.catalog_id: item for item in snapshot.entries}
            original_entries = dict(entries)
            errors: list[CatalogSourceError] = []
            for catalog_id, candidate in incoming_by_id.items():
                if (
                    candidate.source_id != source_id
                    or candidate.source_type is not source_type
                ):
                    raise ValueError("catalog entry source does not match merge source")
                current = entries.get(catalog_id)
                if current is not None and (
                    current.content_hash != candidate.content_hash
                    or current.immutable_ref != candidate.immutable_ref
                ):
                    error = _source_error(
                        code="source.immutable_conflict",
                        source_id=source_id,
                        error_type="CatalogConflictError",
                        occurred_at=observed_at,
                    )
                    errors.append(error)
                    if raise_on_conflict:
                        raise CatalogConflictError(
                            "catalog source attempted to replace an immutable pin"
                        )
                    continue
                if current is not None:
                    candidate = replace(
                        candidate,
                        first_seen_at=current.first_seen_at,
                        last_seen_at=observed_at,
                        source_present=True,
                        federated=(
                            replace(candidate.federated, source_present=True)
                            if candidate.federated is not None
                            else None
                        ),
                    )
                entries[catalog_id] = candidate
            if errors and fail_entire_source_on_conflict:
                entries = original_entries
            elif complete:
                for catalog_id, current in tuple(entries.items()):
                    if (
                        current.source_id == source_id
                        and catalog_id not in incoming_by_id
                    ):
                        entries[catalog_id] = replace(
                            current,
                            source_present=False,
                            federated=(
                                replace(current.federated, source_present=False)
                                if current.federated is not None
                                else None
                            ),
                        )
            if len(entries) > MAX_CATALOG_ENTRIES:
                raise CatalogStateError("catalog contains too many entries")
            source_states = {item.source_id: item for item in snapshot.sources}
            previous = source_states.get(source_id)
            if previous is not None and previous.source_type is not source_type:
                raise CatalogConflictError("catalog source id is owned by another type")
            combined_errors = _bounded_errors(
                *(previous.errors if previous is not None else ()), *errors
            )
            attempt_succeeded = not errors
            source_states[source_id] = CatalogSourceState(
                source_id=source_id,
                source_type=source_type,
                last_attempt_at=observed_at,
                last_success_at=(
                    observed_at
                    if attempt_succeeded
                    else (previous.last_success_at if previous is not None else None)
                ),
                last_attempt_succeeded=attempt_succeeded,
                complete=complete and attempt_succeeded,
                entry_count=sum(
                    item.source_id == source_id for item in entries.values()
                ),
                cursor=cursor if attempt_succeeded else None,
                retry_count=retry_count,
                next_retry_at=next_retry_at,
                etag=etag,
                freshness_expires_at=freshness_expires_at,
                errors=combined_errors,
            )
            updated = CatalogSnapshot(
                revision=snapshot.revision + 1,
                updated_at=observed_at,
                entries=tuple(sorted(entries.values(), key=_entry_sort_key)),
                sources=tuple(
                    sorted(source_states.values(), key=lambda item: item.source_id)
                ),
            )
            self._write_unlocked(updated)
        return CatalogSyncResult(
            success=not errors,
            fetched_count=len(incoming),
            stored_count=sum(item.source_id == source_id for item in updated.entries),
            errors=tuple(errors),
        )

    def _record_source_failure(
        self,
        *,
        source_id: str,
        source_type: CatalogSourceType,
        error: CatalogSourceError,
        observed_at: str,
        retry_count: int | None = None,
        next_retry_at: str | None = None,
    ) -> CatalogSyncResult:
        self._ensure_root()
        with exclusive_file_lock(self.path):
            snapshot = self._read_unlocked()
            source_states = {item.source_id: item for item in snapshot.sources}
            previous = source_states.get(source_id)
            if previous is not None and previous.source_type is not source_type:
                raise CatalogConflictError("catalog source id is owned by another type")
            source_states[source_id] = CatalogSourceState(
                source_id=source_id,
                source_type=source_type,
                last_attempt_at=observed_at,
                last_success_at=(
                    previous.last_success_at if previous is not None else None
                ),
                last_attempt_succeeded=False,
                complete=False,
                entry_count=sum(
                    item.source_id == source_id for item in snapshot.entries
                ),
                cursor=previous.cursor if previous is not None else None,
                retry_count=(
                    retry_count
                    if retry_count is not None
                    else min(
                        previous.retry_count + 1 if previous is not None else 1,
                        100,
                    )
                ),
                next_retry_at=next_retry_at,
                etag=previous.etag if previous is not None else None,
                freshness_expires_at=(
                    previous.freshness_expires_at if previous is not None else None
                ),
                errors=_bounded_errors(
                    *(previous.errors if previous is not None else ()), error
                ),
            )
            updated = CatalogSnapshot(
                revision=snapshot.revision + 1,
                updated_at=observed_at,
                entries=snapshot.entries,
                sources=tuple(
                    sorted(source_states.values(), key=lambda item: item.source_id)
                ),
            )
            self._write_unlocked(updated)
        return CatalogSyncResult(
            success=False,
            fetched_count=0,
            stored_count=sum(item.source_id == source_id for item in updated.entries),
            errors=(error,),
        )

    def merge_federated_source(
        self,
        *,
        source_id: str,
        incoming: Sequence[CatalogEntry],
        observed_at: str,
        freshness_expires_at: str,
        etag: str | None = None,
    ) -> CatalogSyncResult:
        """Atomically publish one complete federated source snapshot."""
        return self._merge_source(
            source_id=source_id,
            source_type=CatalogSourceType.FEDERATED_CATALOG,
            incoming=incoming,
            observed_at=observed_at,
            complete=True,
            fail_entire_source_on_conflict=True,
            cursor=None,
            retry_count=0,
            next_retry_at=None,
            etag=etag,
            freshness_expires_at=freshness_expires_at,
        )

    def record_federated_failure(
        self,
        *,
        source_id: str,
        code: str,
        error_type: str,
        observed_at: str,
        retry_count: int,
        next_retry_at: str,
    ) -> CatalogSyncResult:
        """Retain last-good federated entries and bounded retry state."""
        return self._record_source_failure(
            source_id=source_id,
            source_type=CatalogSourceType.FEDERATED_CATALOG,
            error=_source_error(
                code=code,
                source_id=source_id,
                error_type=error_type,
                occurred_at=observed_at,
            ),
            observed_at=observed_at,
            retry_count=retry_count,
            next_retry_at=next_retry_at,
        )

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise CatalogStateError("integration catalog root cannot be a symlink")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink() or self.lock_path.is_symlink():
            raise CatalogStateError("integration catalog state cannot be a symlink")
        try:
            os.chmod(self.root, 0o700)
        except OSError:  # pragma: no cover - permission hardening is best effort
            pass

    def _read_unlocked(self) -> CatalogSnapshot:
        if not self.path.exists():
            return CatalogSnapshot(revision=0, updated_at=None, entries=(), sources=())
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return _snapshot_from_dict(payload)
        except CatalogStateError:
            raise
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise CatalogStateError("integration catalog state is unreadable") from exc

    def _write_unlocked(self, snapshot: CatalogSnapshot) -> None:
        payload = _snapshot_to_dict(snapshot)
        content = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        _atomic_write_private(self.path, content)


__all__ = [name for name in globals() if not name.startswith("__")]
