"""Bounded event persistence classification and active-session appenders."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from enum import Enum
from typing import Protocol

from gigaloom.sessions.models import HarnessStoredEvent
from gigaloom.sessions.redaction import (
    redact_event_payload,
    redact_for_storage,
)

MAX_EVENT_APPEND_BATCH_RECORDS = 64

_PRESENTATION_DELTA_TYPES = frozenset(
    {
        "delta",
        "message_delta",
        "output_delta",
        "reasoning_delta",
        "stderr_delta",
        "stdout_delta",
        "tool_call_delta",
    }
)
_FINAL_STATE_TYPES = frozenset(
    {
        "command_completed",
        "error",
        "external_turn_completed",
        "message_completed",
        "process_stopped",
        "run_canceled",
        "run_finished",
        "test_completed",
        "tool_call_finished",
        "turn_failed",
    }
)


class EventPersistenceClass(str, Enum):
    """Durability class for one retained streaming event."""

    CRITICAL_CONTROL = "critical_control"
    FINAL_STATE = "final_state"
    PRESENTATION_DELTA = "presentation_delta"


def classify_event_persistence(event_type: str) -> EventPersistenceClass:
    """Classify an event conservatively for synchronous durability boundaries."""
    normalized = str(event_type).strip().lower()
    if normalized in _PRESENTATION_DELTA_TYPES:
        return EventPersistenceClass.PRESENTATION_DELTA
    if normalized in _FINAL_STATE_TYPES:
        return EventPersistenceClass.FINAL_STATE
    return EventPersistenceClass.CRITICAL_CONTROL


def event_forces_flush(event: HarnessStoredEvent) -> bool:
    """Return whether this control or final event ends the current write group."""
    return (
        classify_event_persistence(event.type)
        is not EventPersistenceClass.PRESENTATION_DELTA
    )


class SessionEventAppender(Protocol):
    """Bounded synchronous appender bound to one already validated session."""

    @property
    def session_id(self) -> str:
        """Return the exact session accepted by this appender."""

    def append(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        """Persist one event before returning."""

    def append_many(
        self,
        events: Iterable[HarnessStoredEvent],
    ) -> tuple[HarnessStoredEvent, ...]:
        """Persist one already accumulated bounded event group before returning."""


class EventPersistenceStore(Protocol):
    """Store boundary for single and already accumulated event writes."""

    def append_event(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        """Persist one event before returning."""

    def append_events(
        self,
        events: Iterable[HarnessStoredEvent],
    ) -> tuple[HarnessStoredEvent, ...]:
        """Persist one already accumulated bounded event group before returning."""

    def event_appender(self, session_id: str) -> SessionEventAppender:
        """Return a reusable bounded appender for one active session."""


class BoundedSessionEventAppender:
    """Validate and redact bounded event groups before authoritative persistence."""

    def __init__(
        self,
        session_id: str,
        persist: Callable[
            [tuple[HarnessStoredEvent, ...]],
            tuple[HarnessStoredEvent, ...],
        ],
    ) -> None:
        self._session_id = session_id
        self._persist = persist

    @property
    def session_id(self) -> str:
        """Return the exact session accepted by this appender."""
        return self._session_id

    def append(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        """Persist one event with the legacy synchronous durability guarantee."""
        return self.append_many((event,))[0]

    def append_many(
        self,
        events: Iterable[HarnessStoredEvent],
    ) -> tuple[HarnessStoredEvent, ...]:
        """Persist an already accumulated group with bounded memory and writes."""
        records = tuple(events)
        if not records:
            return ()
        if len(records) > MAX_EVENT_APPEND_BATCH_RECORDS:
            raise ValueError(
                "event append batch must contain at most "
                f"{MAX_EVENT_APPEND_BATCH_RECORDS} records"
            )
        prepared = tuple(_prepare_event(event, self._session_id) for event in records)
        return self._persist(prepared)


class InMemoryEventPersistenceMixin:
    """Apply the bounded event contract to the in-memory compatibility store."""

    def append_event(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        return self.event_appender(event.session_id).append(event)

    def append_events(
        self,
        events: Iterable[HarnessStoredEvent],
    ) -> tuple[HarnessStoredEvent, ...]:
        records = tuple(events)
        if not records:
            return ()
        return self.event_appender(records[0].session_id).append_many(records)

    def event_appender(self, session_id: str) -> SessionEventAppender:
        self.get_session(session_id)
        return BoundedSessionEventAppender(
            session_id,
            self._append_prepared_events,
        )

    def _append_prepared_events(
        self,
        events: tuple[HarnessStoredEvent, ...],
    ) -> tuple[HarnessStoredEvent, ...]:
        for stored in events:
            self._events.setdefault(stored.session_id, []).append(stored)
            self._query_index.record_event(stored)
        for stored in events:
            self.event_broker.publish(stored)
        return events


def _prepare_event(
    event: HarnessStoredEvent,
    session_id: str,
) -> HarnessStoredEvent:
    if event.session_id != session_id:
        raise ValueError("event belongs to another session")
    return replace(
        event,
        message=str(redact_for_storage(event.message)),
        payload=redact_event_payload(event.payload),
    )
