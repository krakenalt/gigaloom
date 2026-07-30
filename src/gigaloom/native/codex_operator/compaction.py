"""Project exact native Codex context compaction lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import time
from typing import Any, Protocol

from gigaloom.native.codex_operator.contracts import (
    CodexCompatibilitySnapshot,
)


CODEX_COMPACTION_TIMEOUT_SECONDS = 30.0
MAX_CODEX_COMPACTION_MESSAGES = 512
_UNKNOWN_OMISSIONS = (
    "complete_native_context",
    "hidden_reasoning",
    "provider_owned_state",
)


class CodexCompactionStatus(str, Enum):
    """Truthful result of one native compaction request."""

    COMPLETED = "completed"
    FAILED = "failed"
    NATIVE_ONLY = "native_only"


@dataclass(frozen=True)
class CodexCompactionBoundary:
    """Observable upstream boundary passed to the ContextManifest owner."""

    binding_id: str
    native_thread_digest: str
    upstream_turn_id: str
    upstream_item_id: str
    previous_manifest_digest: str
    omissions: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class CodexCompactionOutcome:
    """Content-free native compaction result."""

    status: CodexCompactionStatus
    reason_code: str
    message: str
    boundary: CodexCompactionBoundary | None = None


class _AppServerClient(Protocol):
    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float,
    ) -> Mapping[str, Any]: ...

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None: ...

    def respond(
        self,
        request_id: str | int,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None: ...


class CodexNativeCompactionService:
    """Call upstream compaction and accept only its exact item lifecycle."""

    def __init__(
        self,
        compatibility: CodexCompatibilitySnapshot,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.compatibility = compatibility
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic

    def compact(
        self,
        client: _AppServerClient,
        *,
        binding_id: str,
        native_thread_id: str,
        cwd: str,
        previous_manifest_digest: str,
        manifest_revision_hook: Callable[[CodexCompactionBoundary], None],
        timeout_seconds: float = CODEX_COMPACTION_TIMEOUT_SECONDS,
    ) -> CodexCompactionOutcome:
        """Compact one loaded thread or expose the truthful native-only fallback."""
        if not self.compatibility.structured:
            return CodexCompactionOutcome(
                status=CodexCompactionStatus.NATIVE_ONLY,
                reason_code="structured_compaction_not_admitted",
                message="Use native /compact inside the Codex TUI.",
            )
        _identity(binding_id)
        _identity(native_thread_id)
        _validate_digest(previous_manifest_digest)
        if timeout_seconds <= 0:
            raise ValueError("Codex compaction timeout is invalid")
        try:
            resumed = client.request(
                "thread/resume",
                {"threadId": native_thread_id, "cwd": cwd},
                timeout=min(timeout_seconds, 10.0),
            )
            response_thread_id = _thread_id(resumed)
            if response_thread_id != native_thread_id:
                return _failed("resumed_thread_identity_changed")
            accepted = client.request(
                "thread/compact/start",
                {"threadId": native_thread_id},
                timeout=min(timeout_seconds, 10.0),
            )
            if accepted:
                return _failed("compact_acceptance_payload_changed")
            boundary = self._wait_for_boundary(
                client,
                binding_id=binding_id,
                native_thread_id=native_thread_id,
                previous_manifest_digest=previous_manifest_digest,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return _failed("native_compaction_request_failed")
        if boundary is None:
            return _failed("native_compaction_lifecycle_incomplete")
        try:
            manifest_revision_hook(boundary)
        except Exception:
            return _failed("manifest_revision_hook_failed")
        return CodexCompactionOutcome(
            status=CodexCompactionStatus.COMPLETED,
            reason_code="upstream_context_compaction_completed",
            message="Native Codex context compaction completed.",
            boundary=boundary,
        )

    def _wait_for_boundary(
        self,
        client: _AppServerClient,
        *,
        binding_id: str,
        native_thread_id: str,
        previous_manifest_digest: str,
        timeout_seconds: float,
    ) -> CodexCompactionBoundary | None:
        deadline = self._monotonic() + timeout_seconds
        started: tuple[str, str] | None = None
        for _ in range(MAX_CODEX_COMPACTION_MESSAGES):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return None
            message = client.next_message(timeout=min(remaining, 0.25))
            if message is None:
                continue
            method = str(message.get("method") or "")
            if "id" in message and method:
                client.respond(
                    message["id"],
                    error={
                        "code": -32601,
                        "message": "unsupported during compaction",
                    },
                )
                continue
            params = _mapping(message.get("params"))
            if str(params.get("threadId") or "") != native_thread_id:
                continue
            item = _mapping(params.get("item"))
            if str(item.get("type") or "") != "contextCompaction":
                continue
            turn_id = _identity(params.get("turnId"))
            item_id = _identity(item.get("id"))
            if method == "item/started":
                started = turn_id, item_id
                continue
            if method == "item/completed" and started == (turn_id, item_id):
                return CodexCompactionBoundary(
                    binding_id=binding_id,
                    native_thread_digest=hashlib.sha256(
                        native_thread_id.encode("utf-8")
                    ).hexdigest(),
                    upstream_turn_id=turn_id,
                    upstream_item_id=item_id,
                    previous_manifest_digest=previous_manifest_digest,
                    omissions=_UNKNOWN_OMISSIONS,
                    created_at=self._timestamp(),
                )
        return None

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Codex compaction clock must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()


def _failed(reason_code: str) -> CodexCompactionOutcome:
    return CodexCompactionOutcome(
        status=CodexCompactionStatus.FAILED,
        reason_code=reason_code,
        message="Native Codex context compaction did not complete.",
    )


def _thread_id(value: Mapping[str, Any]) -> str:
    thread = _mapping(value.get("thread"))
    return _identity(thread.get("id"))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _identity(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Codex compaction identity is invalid")
    text = value.strip()
    if not text or text != value or len(text) > 256:
        raise ValueError("Codex compaction identity is invalid")
    return text


def _validate_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("previous ContextManifest digest is invalid")
