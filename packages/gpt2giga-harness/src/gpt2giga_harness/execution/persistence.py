"""Persistence boundary for runner-owned raw records and events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from gpt2giga_harness.sessions.api import HarnessStoredEvent

MAX_PROVENANCE_EVENTS = 100


@dataclass(frozen=True)
class RunPersistenceService:
    """Persist runner evidence through one supplied authoritative store."""

    store: Any
    id_factory: Callable[[str], str]
    clock: Callable[[], str]
    _event_ids_by_run: dict[str, list[str]] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def append_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        message: str,
        payload: Mapping[str, Any],
    ) -> HarnessStoredEvent:
        """Append one normalized stored event."""
        stored = self.store.append_event(
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
        event_ids = self._event_ids_by_run.setdefault(run_id, [])
        event_ids.append(stored.id)
        if len(event_ids) > MAX_PROVENANCE_EVENTS:
            del event_ids[:-MAX_PROVENANCE_EVENTS]
        return stored

    def provenance_events(self, run_id: str) -> tuple[HarnessStoredEvent, ...]:
        """Resolve the bounded current-run evidence window by direct identity."""
        getter = getattr(self.store, "get_event", None)
        if not callable(getter):
            return ()
        return tuple(
            getter(event_id) for event_id in self._event_ids_by_run.get(run_id, ())
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
