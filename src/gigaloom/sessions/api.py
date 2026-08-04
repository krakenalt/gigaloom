"""Public bounded query contract for session records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gigaloom.sessions.conversation import (
    EDITED_FROM_MESSAGE_ID as EDITED_FROM_MESSAGE_ID,
)
from gigaloom.sessions.catalog_binding import (
    session_catalog_project_id as session_catalog_project_id,
)
from gigaloom.sessions.event_stream import EventTailPage
from gigaloom.sessions.models import (
    HarnessMessage as HarnessMessage,
    HarnessRawRecord as HarnessRawRecord,
    HarnessRun as HarnessRun,
    HarnessSession as HarnessSession,
    HarnessSessionBundle as HarnessSessionBundle,
    HarnessStoredEvent as HarnessStoredEvent,
)
from gigaloom.sessions.thread_relay import (
    MAX_THREAD_ATTACHMENT_REFS as MAX_THREAD_ATTACHMENT_REFS,
    MAX_THREAD_CURSOR_CHARS as MAX_THREAD_CURSOR_CHARS,
    MAX_THREAD_MESSAGE_CHARS as MAX_THREAD_MESSAGE_CHARS,
    MAX_THREAD_DELIVERY_PAGE_SIZE as MAX_THREAD_DELIVERY_PAGE_SIZE,
    MAX_THREAD_OUTSTANDING_CHILDREN as MAX_THREAD_OUTSTANDING_CHILDREN,
    MAX_THREAD_RELAY_DEPTH as MAX_THREAD_RELAY_DEPTH,
    MAX_THREAD_VISIBLE_CONTENT_CHARS as MAX_THREAD_VISIBLE_CONTENT_CHARS,
    MAX_THREAD_VISIBLE_MESSAGES as MAX_THREAD_VISIBLE_MESSAGES,
    THREAD_RELAY_SCHEMA_VERSION as THREAD_RELAY_SCHEMA_VERSION,
    THREAD_DELIVERY_STORE_SCHEMA_VERSION as THREAD_DELIVERY_STORE_SCHEMA_VERSION,
    ThreadActiveTurnV1 as ThreadActiveTurnV1,
    ThreadAuthorMode as ThreadAuthorMode,
    ThreadDeliveryIntent as ThreadDeliveryIntent,
    ThreadDeliveryCapacityError as ThreadDeliveryCapacityError,
    ThreadDeliveryConflictError as ThreadDeliveryConflictError,
    ThreadDeliveryCursor as ThreadDeliveryCursor,
    ThreadDeliveryIntegrityError as ThreadDeliveryIntegrityError,
    ThreadDeliveryNotFoundError as ThreadDeliveryNotFoundError,
    ThreadDeliveryPage as ThreadDeliveryPage,
    ThreadDeliveryRecord as ThreadDeliveryRecord,
    ThreadDeliveryRepository as ThreadDeliveryRepository,
    ThreadDeliveryRepositoryError as ThreadDeliveryRepositoryError,
    ThreadDeliveryReservation as ThreadDeliveryReservation,
    ThreadDeliveryReceiptV1 as ThreadDeliveryReceiptV1,
    ThreadDeliveryStatus as ThreadDeliveryStatus,
    ThreadLocatorV1 as ThreadLocatorV1,
    ThreadMessageEnvelopeV1 as ThreadMessageEnvelopeV1,
    ThreadReadProjectionV1 as ThreadReadProjectionV1,
    ThreadRelationshipKind as ThreadRelationshipKind,
    ThreadRelationshipV1 as ThreadRelationshipV1,
    ThreadSourceKind as ThreadSourceKind,
    ThreadVisibleMessageV1 as ThreadVisibleMessageV1,
    ThreadVisibleRole as ThreadVisibleRole,
    thread_delivery_receipt_digest as thread_delivery_receipt_digest,
    thread_delivery_receipt_from_dict as thread_delivery_receipt_from_dict,
    thread_delivery_receipt_to_dict as thread_delivery_receipt_to_dict,
    thread_locator_digest as thread_locator_digest,
    thread_locator_from_dict as thread_locator_from_dict,
    thread_locator_to_dict as thread_locator_to_dict,
    thread_message_content_digest as thread_message_content_digest,
    thread_message_envelope_digest as thread_message_envelope_digest,
    thread_message_envelope_from_dict as thread_message_envelope_from_dict,
    thread_message_envelope_to_dict as thread_message_envelope_to_dict,
    thread_read_projection_digest as thread_read_projection_digest,
    thread_read_projection_from_dict as thread_read_projection_from_dict,
    thread_read_projection_to_dict as thread_read_projection_to_dict,
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
