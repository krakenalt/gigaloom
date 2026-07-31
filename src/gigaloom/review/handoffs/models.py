"""Review models primitives."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping, Protocol
from gigaloom.review.ports import EnvironmentSnapshot


HANDOFF_CAPSULE_SCHEMA_VERSION = 1


MAX_CAPSULE_ARTIFACTS = 100


MAX_CAPSULE_QUESTIONS = 100


_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@~-]{0,255}\Z")


_QUESTION_REQUEST_MARKERS = ("elicitation", "input_request", "input_requested")


_QUESTION_RESOLUTION_MARKERS = (
    "elicitation_response",
    "input_answer",
    "input_response",
)


class HandoffCapsuleError(ValueError):
    """Raised when a handoff capsule cannot be built or verified truthfully."""


class EnvironmentSnapshotProvider(Protocol):
    """Minimal read-only Environment owner used by capsule construction."""

    def snapshot(self, workspace: str | Path) -> EnvironmentSnapshot: ...


@dataclass(frozen=True)
class HandoffCapsule:
    """Strict schema-v1 capsule whose identity excludes all raw content."""

    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible capsule document."""
        return json.loads(json.dumps(self.payload))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> HandoffCapsule:
        """Verify and parse one exact schema-v1 capsule."""
        from .validation import verify_handoff_capsule

        return cls(payload=verify_handoff_capsule(payload))
