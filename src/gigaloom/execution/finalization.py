"""Terminal result projection for one Harness invocation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol

from gigaloom.types import HarnessEventType, HarnessResult


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class RunCompletionArtifactV1:
    """Content-free binding produced by one injected completion hook."""

    kind: str
    artifact_id: str
    sha256: str
    status: str
    attributes: Mapping[str, str | bool | None]

    def __post_init__(self) -> None:
        for value, name in (
            (self.kind, "completion artifact kind"),
            (self.artifact_id, "completion artifact id"),
            (self.status, "completion artifact status"),
        ):
            if _IDENTITY_RE.fullmatch(value) is None:
                raise ValueError(f"{name} is invalid")
        if _SHA256_RE.fullmatch(self.sha256) is None:
            raise ValueError("completion artifact sha256 is invalid")
        if len(self.attributes) > 32:
            raise ValueError("completion artifact attributes exceed the limit")
        for key, value in self.attributes.items():
            if _IDENTITY_RE.fullmatch(key) is None:
                raise ValueError("completion artifact attribute key is invalid")
            if value is not None and not isinstance(value, (str, bool)):
                raise ValueError("completion artifact attribute value is invalid")
            if isinstance(value, str) and len(value) > 512:
                raise ValueError("completion artifact attribute value is too long")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached persistence-safe projection."""
        return {
            "schema_version": 1,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "sha256": self.sha256,
            "status": self.status,
            "attributes": dict(sorted(self.attributes.items())),
        }


class RunCompletionHook(Protocol):
    """Dependency-injection port invoked only after a run is terminal."""

    def on_run_completed(
        self,
        run_id: str,
        *,
        completed_at: str,
    ) -> tuple[RunCompletionArtifactV1, ...]:
        """Return bounded content-free artifacts captured for the run."""
        ...


@dataclass(frozen=True)
class TerminalRunOutcome:
    """Normalized terminal state persisted by the runner facade."""

    status: str
    role: str
    content: str
    event_type: str
    event_message: str
    error: str | None
    message_metadata: Mapping[str, Any]


class RunFinalizationService:
    """Project invocation state into one deterministic terminal outcome."""

    def __init__(self, completion_hook: RunCompletionHook | None = None) -> None:
        self._completion_hook = completion_hook

    def resolve(
        self,
        result: HarnessResult,
        *,
        canceled: bool,
        latest_usage: Mapping[str, Any],
        reasoning: str,
    ) -> TerminalRunOutcome:
        """Resolve status, message, event, and error fields exactly once."""
        if canceled:
            status = "canceled"
            role = "error"
            content = "Harness run canceled."
            event_type = HarnessEventType.RUN_CANCELED.value
            event_message = "Harness run canceled."
            error = content
        elif result.ok:
            status = "succeeded"
            role = "assistant"
            content = result.text
            event_type = HarnessEventType.MESSAGE_COMPLETED.value
            event_message = "Assistant message completed."
            error = None
        else:
            status = "failed"
            role = "error"
            content = result.error or result.text or "Harness run failed"
            event_type = HarnessEventType.ERROR.value
            event_message = "Harness run failed."
            error = content
        message_metadata: dict[str, Any] = {}
        if role == "assistant" and latest_usage:
            message_metadata["usage"] = dict(latest_usage)
        if role == "assistant" and reasoning:
            message_metadata["reasoning"] = reasoning
        return TerminalRunOutcome(
            status=status,
            role=role,
            content=content,
            event_type=event_type,
            event_message=event_message,
            error=error,
            message_metadata=message_metadata,
        )

    def capture(
        self,
        run_id: str,
        completed_at: str,
    ) -> dict[str, Any]:
        """Invoke the post-terminal hook and return persistence metadata."""
        if self._completion_hook is None:
            return {}
        artifacts = self._completion_hook.on_run_completed(
            run_id,
            completed_at=completed_at,
        )
        if not isinstance(artifacts, tuple) or not all(
            isinstance(item, RunCompletionArtifactV1) for item in artifacts
        ):
            raise ValueError("run completion hook returned invalid artifacts")
        identities = [(item.kind, item.artifact_id) for item in artifacts]
        if identities != sorted(set(identities)):
            raise ValueError("run completion artifacts must be sorted and unique")
        return {"completion_artifacts": [artifact.to_dict() for artifact in artifacts]}
