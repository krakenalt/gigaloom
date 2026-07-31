"""Bounded source-bound cache for context manifests."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock

from gigaloom.contracts.context import (
    ContextManifest,
    _validate_identifier,
    _validate_sha256,
)


class StaleContextManifestError(LookupError):
    """Raised when a cache entry is bound to different source inputs."""


class ContextManifestCache:
    """Small LRU cache that validates source and configuration bindings."""

    def __init__(self, *, max_entries: int = 128) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, ContextManifest] = OrderedDict()
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def put(self, key: str, manifest: ContextManifest) -> None:
        """Store a verified manifest under an application-owned cache key."""
        _validate_identifier(key, "cache key")
        with self._lock:
            self._entries[key] = manifest
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def get(
        self,
        key: str,
        *,
        source_revision: str,
        config_digest: str,
    ) -> ContextManifest | None:
        """Return a bound entry, rejecting stale source or configuration."""
        _validate_identifier(key, "cache key")
        _validate_identifier(source_revision, "source_revision")
        _validate_sha256(config_digest, "config_digest")
        with self._lock:
            manifest = self._entries.get(key)
            if manifest is None:
                return None
            if manifest.source_revision != source_revision:
                self._entries.pop(key, None)
                raise StaleContextManifestError(
                    "cached context manifest has a stale source revision"
                )
            if manifest.config_digest != config_digest:
                self._entries.pop(key, None)
                raise StaleContextManifestError(
                    "cached context manifest has a stale configuration digest"
                )
            self._entries.move_to_end(key)
            return manifest

    def clear(self) -> None:
        """Drop all projections without retaining stale source bindings."""
        with self._lock:
            self._entries.clear()


__all__ = ["ContextManifestCache", "StaleContextManifestError"]
