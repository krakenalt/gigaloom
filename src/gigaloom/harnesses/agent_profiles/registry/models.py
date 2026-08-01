"""Immutable runtime projection of one validated ACP Registry snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re

from gigaloom.contracts import ACPRegistryEntryV1, ACPRegistrySnapshotV1


_REGISTRY_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_ERROR_CODE_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


@dataclass(frozen=True, slots=True)
class ACPRegistryCatalog:
    """One immutable, execution-inert registry revision and availability state."""

    registry_version: str
    snapshot: ACPRegistrySnapshotV1
    entries: tuple[ACPRegistryEntryV1, ...]
    offline: bool = False
    from_cache: bool = False
    refresh_error_code: str | None = None

    def __post_init__(self) -> None:
        if _REGISTRY_VERSION_RE.fullmatch(self.registry_version) is None:
            raise ValueError("ACP registry version is invalid")
        if not isinstance(self.snapshot, ACPRegistrySnapshotV1):
            raise ValueError("ACP registry snapshot is invalid")
        if not isinstance(self.entries, tuple) or any(
            not isinstance(entry, ACPRegistryEntryV1) for entry in self.entries
        ):
            raise ValueError("ACP registry entries must be a tuple")
        ordered = tuple(sorted(self.entries, key=lambda entry: entry.registry_id))
        if ordered != self.entries:
            raise ValueError("ACP registry entries must be ordered by registry id")
        ids = tuple(entry.registry_id for entry in self.entries)
        if len(set(ids)) != len(ids):
            raise ValueError("ACP registry ids must be unique")
        if len(self.entries) != self.snapshot.entry_count:
            raise ValueError("ACP registry entry count does not match the snapshot")
        if any(
            entry.snapshot_digest != self.snapshot.snapshot_digest
            for entry in self.entries
        ):
            raise ValueError("ACP registry entry is bound to another snapshot")
        if _entries_digest(self.entries) != self.snapshot.entries_digest:
            raise ValueError("ACP registry entries digest does not match the snapshot")
        if not isinstance(self.offline, bool) or not isinstance(self.from_cache, bool):
            raise ValueError("ACP registry availability flags must be boolean")
        if self.offline and not self.from_cache:
            raise ValueError("offline ACP registry state must come from cache")
        if self.refresh_error_code is not None and (
            _ERROR_CODE_RE.fullmatch(self.refresh_error_code) is None
        ):
            raise ValueError("ACP registry refresh error code is invalid")

    def as_cached(
        self,
        *,
        stale: bool | None = None,
        offline: bool = False,
        refresh_error_code: str | None = None,
    ) -> ACPRegistryCatalog:
        """Project the same immutable revision as cached/stale/offline evidence."""
        snapshot = self.snapshot
        if stale is not None and stale != snapshot.stale:
            snapshot = replace(snapshot, stale=stale)
        return replace(
            self,
            snapshot=snapshot,
            offline=offline,
            from_cache=True,
            refresh_error_code=refresh_error_code,
        )


def registry_entries_digest(entries: tuple[ACPRegistryEntryV1, ...]) -> str:
    """Return the canonical ordered digest for one decoded entry collection."""
    return _entries_digest(entries)


def _entries_digest(entries: tuple[ACPRegistryEntryV1, ...]) -> str:
    payload = json.dumps(
        [entry.entry_digest for entry in entries],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
