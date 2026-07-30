"""Persistence boundary for runner-owned raw records and events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from gpt2giga_harness.execution.milestones import PersistenceMilestone
from gpt2giga_harness.sessions import (
    HarnessMessage,
    HarnessStoredEvent,
    RunPatch,
    SessionWriteBatch,
    SessionWriteBatchResult,
)

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
            self.event(
                session_id,
                run_id,
                event_type,
                message,
                payload,
            )
        )
        self._remember_events((stored,))
        return stored

    def event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        message: str,
        payload: Mapping[str, Any],
    ) -> HarnessStoredEvent:
        """Prepare one normalized event for a milestone write batch."""
        return HarnessStoredEvent(
            id=self.id_factory("evt"),
            session_id=session_id,
            run_id=run_id,
            type=event_type,
            message=message,
            payload=payload,
            created_at=self.clock(),
        )

    def persist_milestone(
        self,
        milestone: PersistenceMilestone,
        *,
        session_id: str,
        run_id: str,
        run_patch: Mapping[str, Any] | None = None,
        messages: tuple[HarnessMessage, ...] = (),
        events: tuple[HarnessStoredEvent, ...] = (),
    ) -> SessionWriteBatchResult:
        """Persist one bounded runner milestone through the frozen batch contract."""
        result = self.store.apply_write_batch(
            SessionWriteBatch(
                batch_id=f"runner:{run_id}:{milestone.value}",
                session_id=session_id,
                run_patches=(
                    (RunPatch(run_id, run_patch),) if run_patch is not None else ()
                ),
                messages=messages,
                events=events,
            )
        )
        self._remember_events(result.events)
        return result

    def _remember_events(self, events: tuple[HarnessStoredEvent, ...]) -> None:
        """Retain a bounded direct-lookup window for provenance assembly."""
        for stored in events:
            event_ids = self._event_ids_by_run.setdefault(stored.run_id, [])
            event_ids.append(stored.id)
            if len(event_ids) > MAX_PROVENANCE_EVENTS:
                del event_ids[:-MAX_PROVENANCE_EVENTS]

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
