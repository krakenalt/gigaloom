"""Review models primitives."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping


TRACE_REPLAY_SCHEMA_VERSION = 1


MAX_TRACE_REPLAY_TARGET_CHARS = 512


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+~-]{0,255}\Z")


_ACTIVE_STATUSES = frozenset({"queued", "running", "retry_wait"})


_TRACE_REPLAY_FIELDS = frozenset({"axis", "target", "manifest_sha256"})


class TraceReplayAxis(str, Enum):
    """One execution dimension that a Trace-to-Replay may change."""

    MODEL = "model"
    PROVIDER = "provider"
    HARNESS = "harness"
    EXTENSIONS = "extensions"


class TraceReplayConflictError(ValueError):
    """Raised when retained evidence no longer matches an reviewed manifest."""


@dataclass(frozen=True)
class TraceReplayManifest:
    """Immutable replay identity with one changed and all unchanged dimensions."""

    source_run_id: str
    source_session_id: str
    task_sha256: str
    source_evidence_sha256: str
    axis: TraceReplayAxis
    source_dimensions: Mapping[str, Any]
    target_dimensions: Mapping[str, Any]
    fixed_dimensions: Mapping[str, Any]
    unchanged_snapshot_sha256: str
    created_at: str
    manifest_sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the manifest without retaining prompt or tool contents."""
        return {
            "schema_version": TRACE_REPLAY_SCHEMA_VERSION,
            "source_run_id": self.source_run_id,
            "source_session_id": self.source_session_id,
            "task_sha256": self.task_sha256,
            "source_evidence_sha256": self.source_evidence_sha256,
            "axis": self.axis.value,
            "source_dimensions": dict(self.source_dimensions),
            "target_dimensions": dict(self.target_dimensions),
            "fixed_dimensions": dict(self.fixed_dimensions),
            "unchanged_snapshot_sha256": self.unchanged_snapshot_sha256,
            "created_at": self.created_at,
            "manifest_sha256": self.manifest_sha256,
            "content_free": True,
        }
