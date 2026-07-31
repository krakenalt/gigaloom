"""Bounded content-free security evidence for MCP App host decisions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True, slots=True)
class MCPAppSecurityEvent:
    """One content-free channel or resource policy event."""

    instance_id: str
    server_id: str
    resource_sha256: str
    code: str
    method: str | None = None
    request_id_digest: str | None = None


class MCPAppEvidenceLog:
    """In-memory bounded audit sink that never stores HTML or message payloads."""

    def __init__(self, *, max_events: int = 256) -> None:
        if max_events <= 0 or max_events > 1024:
            raise ValueError("max_events must be between 1 and 1024")
        self._events: deque[MCPAppSecurityEvent] = deque(maxlen=max_events)
        self._lock = RLock()

    def append(self, event: MCPAppSecurityEvent) -> None:
        """Append one already-redacted event."""
        with self._lock:
            self._events.append(event)

    def snapshot(self) -> tuple[MCPAppSecurityEvent, ...]:
        """Return the bounded event sequence."""
        with self._lock:
            return tuple(self._events)
