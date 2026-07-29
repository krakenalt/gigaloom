"""Persistence boundary for runner-owned raw records and events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from gpt2giga_harness.sessions.api import HarnessStoredEvent


@dataclass(frozen=True)
class RunPersistenceService:
    """Persist runner evidence through one supplied authoritative store."""

    store: Any
    id_factory: Callable[[str], str]
    clock: Callable[[], str]

    def append_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        message: str,
        payload: Mapping[str, Any],
    ) -> HarnessStoredEvent:
        """Append one normalized stored event."""
        return self.store.append_event(
            HarnessStoredEvent(
                id=self.id_factory("evt"),
                session_id=session_id,
                run_id=run_id,
                type=event_type,
                message=message,
                payload=payload,
                created_at=self.clock(),
            )
        )

    def append_raw_request(
        self,
        *,
        session_id: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> Any:
        """Persist one redacted raw request record."""
        return self.store.append_raw_request(
            session_id=session_id,
            run_id=run_id,
            payload=payload,
        )

    def append_raw_response(
        self,
        *,
        session_id: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> Any:
        """Persist one redacted raw response record."""
        return self.store.append_raw_response(
            session_id=session_id,
            run_id=run_id,
            payload=payload,
        )
