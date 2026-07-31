"""Public bounded query contract for session records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gigaloom.sessions.conversation import (
    EDITED_FROM_MESSAGE_ID as EDITED_FROM_MESSAGE_ID,
)
from gigaloom.sessions.event_stream import EventTailPage
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessSessionBundle,
    HarnessStoredEvent,
)

MAX_MESSAGE_QUERY_LIMIT = 1_000
MAX_RECORD_QUERY_LIMIT = 100


class MessageNotFoundError(KeyError):
    """Raised when a retained message identity does not exist."""


class EventNotFoundError(KeyError):
    """Raised when a retained event identity does not exist."""


class StaleReadSnapshotError(ValueError):
    """Raised when a cursor no longer names the current read snapshot."""


def bounded_query_limit(limit: int, *, maximum: int) -> int:
    """Validate an explicit interactive-query bound."""
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


@dataclass(frozen=True)
class RunPageCursor:
    """Stable newest-first position inside one run-index snapshot."""

    generation: int
    session_id: str
    position: int
    run_id: str


@dataclass(frozen=True)
class RunPage:
    """One bounded newest-first page of session runs."""

    items: tuple[HarnessRun, ...]
    next_cursor: RunPageCursor | None
    has_more: bool
    generation: int


class SessionQueryStore(Protocol):
    """Interactive bounded reads kept separate from complete export methods."""

    def list_recent_messages(
        self,
        session_id: str,
        *,
        limit: int,
        before: str | None = None,
        through: str | None = None,
    ) -> tuple[HarnessMessage, ...]:
        """Return an active chronological message window."""

    def get_message(self, message_id: str) -> HarnessMessage:
        """Return one retained message, including superseded evidence."""

    def list_runs_page(
        self,
        session_id: str,
        *,
        cursor: RunPageCursor | None = None,
        limit: int = 50,
    ) -> RunPage:
        """Return one bounded newest-first run page."""

    def list_events_page(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        max_bytes: int = 1024 * 1024,
    ) -> EventTailPage:
        """Return one bounded append-order event page."""

    def get_event(self, event_id: str) -> HarnessStoredEvent:
        """Return one retained event by direct identity lookup."""

    def list_raw_requests_for_run(
        self,
        run_id: str,
        *,
        limit: int = 20,
    ) -> tuple[HarnessRawRecord, ...]:
        """Return a bounded chronological request window for one run."""

    def list_raw_responses_for_run(
        self,
        run_id: str,
        *,
        limit: int = 20,
    ) -> tuple[HarnessRawRecord, ...]:
        """Return a bounded chronological response window for one run."""

    def export_session_bundle(self, session_id: str) -> HarnessSessionBundle:
        """Explicitly export the complete retained session bundle."""
