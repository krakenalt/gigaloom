"""Bounded content-free event replay for Operator Workspace clients."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import re
from threading import Lock
from uuid import uuid4


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@~-]{0,255}\Z")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class OperatorEvent:
    """One content-free change notification retained in process memory."""

    cursor: str
    kind: str
    resource_id: str
    revision: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        """Serialize the event without owner content."""
        return {
            "cursor": self.cursor,
            "kind": self.kind,
            "resource_id": self.resource_id,
            "revision": self.revision,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class OperatorEventPage:
    """Replay result with an explicit resnapshot boundary."""

    events: tuple[OperatorEvent, ...]
    cursor: str
    resnapshot_reason: str | None = None
    has_more: bool = False


@dataclass(slots=True)
class _ScopeState:
    sequence: int
    events: deque[OperatorEvent]


class OperatorEventBroker:
    """Retain a bounded per-owner/workspace replay window."""

    def __init__(self, *, max_events_per_scope: int = 128) -> None:
        if not 1 <= max_events_per_scope <= 4096:
            raise ValueError("operator event retention is outside the supported range")
        self.max_events_per_scope = max_events_per_scope
        self.generation = uuid4().hex[:16]
        self._lock = Lock()
        self._scopes: dict[tuple[str, str], _ScopeState] = {}

    def cursor(self, *, owner_id: str, workspace_id: str) -> str:
        """Return the current content-free cursor for one exact scope."""
        scope = _scope(owner_id, workspace_id)
        with self._lock:
            state = self._scopes.get(scope)
            sequence = state.sequence if state is not None else 0
        return self._cursor(sequence)

    def publish(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        kind: str,
        resource_id: str,
        revision: str,
        sha256: str,
    ) -> OperatorEvent:
        """Append one validated content-free notification."""
        scope = _scope(owner_id, workspace_id)
        checked_kind = _required_token(kind, "kind")
        checked_resource = _required_identity(resource_id, "resource_id")
        checked_revision = _required_identity(revision, "revision")
        checked_sha256 = _required_sha256(sha256, "sha256")
        with self._lock:
            state = self._scopes.get(scope)
            if state is None:
                state = _ScopeState(
                    sequence=0,
                    events=deque(maxlen=self.max_events_per_scope),
                )
                self._scopes[scope] = state
            state.sequence += 1
            event = OperatorEvent(
                cursor=self._cursor(state.sequence),
                kind=checked_kind,
                resource_id=checked_resource,
                revision=checked_revision,
                sha256=checked_sha256,
            )
            state.events.append(event)
        return event

    def read(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        after: str | None,
        limit: int = 100,
    ) -> OperatorEventPage:
        """Read after a cursor or require an explicit authoritative resnapshot."""
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("operator event page limit is outside the supported range")
        scope = _scope(owner_id, workspace_id)
        with self._lock:
            state = self._scopes.get(scope)
            current = state.sequence if state is not None else 0
            retained = tuple(state.events) if state is not None else ()
        current_cursor = self._cursor(current)
        if after is None:
            return OperatorEventPage((), current_cursor, "initial")
        parsed = self._parse_cursor(after)
        if parsed is None:
            return OperatorEventPage((), current_cursor, "cursor_gap")
        generation, sequence = parsed
        if generation != self.generation:
            return OperatorEventPage((), current_cursor, "generation_changed")
        if sequence > current:
            return OperatorEventPage((), current_cursor, "cursor_gap")
        oldest = current - len(retained) + 1
        if retained and sequence < oldest - 1:
            return OperatorEventPage((), current_cursor, "slow_consumer")
        available = tuple(
            event
            for event in retained
            if int(event.cursor.rsplit(".", maxsplit=1)[1]) > sequence
        )
        page = available[:limit]
        cursor = page[-1].cursor if page else self._cursor(sequence)
        return OperatorEventPage(
            events=page,
            cursor=cursor,
            has_more=len(available) > len(page),
        )

    def _cursor(self, sequence: int) -> str:
        return f"op1.{self.generation}.{sequence}"

    @staticmethod
    def _parse_cursor(value: str) -> tuple[str, int] | None:
        parts = str(value).split(".")
        if len(parts) != 3 or parts[0] != "op1":
            return None
        try:
            sequence = int(parts[2])
        except ValueError:
            return None
        if sequence < 0 or not re.fullmatch(r"[0-9a-f]{16}", parts[1]):
            return None
        return parts[1], sequence


def operator_event_sse(event: OperatorEvent) -> str:
    """Encode one update as a bounded SSE frame."""
    payload = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
    return f"id: {event.cursor}\nevent: update\ndata: {payload}\n\n"


def operator_resnapshot_sse(page: OperatorEventPage) -> str:
    """Encode an explicit resnapshot boundary."""
    payload = json.dumps(
        {
            "cursor": page.cursor,
            "reason": page.resnapshot_reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"id: {page.cursor}\nevent: resnapshot\ndata: {payload}\n\n"


def _scope(owner_id: object, workspace_id: object) -> tuple[str, str]:
    return (
        _required_identity(owner_id, "owner_id"),
        _required_identity(workspace_id, "workspace_id"),
    )


def _required_identity(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _required_token(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _required_sha256(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return text
