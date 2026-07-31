"""Bounded exact-snapshot cache for Python Impact Radar indexes."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
from threading import RLock

from gigaloom.projects.impact_index import PythonImpactIndex


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class StalePythonImpactIndexError(LookupError):
    """Raised when an exact requested Impact snapshot is unavailable."""


class PythonImpactIndexCache:
    """Small LRU of immutable indexes addressed by their exact digest."""

    def __init__(self, *, max_entries: int = 8) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], PythonImpactIndex] = OrderedDict()
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def put(
        self,
        project_root: str | Path,
        index: PythonImpactIndex,
    ) -> None:
        """Store one verified immutable index under its exact root and digest."""
        root = _project_root(project_root)
        if root != index._project_root:
            raise ValueError("Impact index is bound to a different project root")
        key = (root, index.index_digest)
        with self._lock:
            self._entries[key] = index
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def get(
        self,
        project_root: str | Path,
        *,
        index_digest: str,
        source_revision: str,
    ) -> PythonImpactIndex:
        """Return only the exact retained snapshot requested by the caller."""
        root = _project_root(project_root)
        _validate_digest(index_digest)
        if not source_revision or len(source_revision) > 256:
            raise ValueError("source_revision must be 1..256 characters")
        key = (root, index_digest)
        with self._lock:
            index = self._entries.get(key)
            if index is None:
                raise StalePythonImpactIndexError(
                    "Impact index snapshot is not retained; resnapshot required"
                )
            if index.source_revision != source_revision:
                raise StalePythonImpactIndexError(
                    "Impact index source revision changed; resnapshot required"
                )
            self._entries.move_to_end(key)
            return index

    def clear(self) -> None:
        """Drop every retained immutable index."""
        with self._lock:
            self._entries.clear()


def _project_root(value: str | Path) -> str:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    return str(root)


def _validate_digest(value: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError("index_digest must be a lowercase SHA-256 digest")


__all__ = ["PythonImpactIndexCache", "StalePythonImpactIndexError"]
