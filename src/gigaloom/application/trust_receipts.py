"""Bounded application API for immutable data-flow receipts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

from gigaloom.contracts import (
    DataFlowReceipt,
    data_flow_receipt_from_dict,
    data_flow_receipt_to_dict,
)
from gigaloom.sessions.event_stream import EventTailPage
from gigaloom.sessions.models import HarnessRun, HarnessStoredEvent
from gigaloom.sessions.store import utc_now


DATA_FLOW_RECEIPT_EVENT_TYPE = "data_flow.receipt"
DATA_FLOW_RECEIPT_EVENT_KIND = "gigaloom.data_flow_receipt.v1"
DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION = 1
MAX_DATA_FLOW_RECEIPT_PAGE_ITEMS = 100
MAX_DATA_FLOW_RECEIPT_PAGE_BYTES = 1024 * 1024


class ReceiptSessionStore(Protocol):
    """Minimal existing session owner surface used by the receipt API."""

    def get_run(self, run_id: str) -> HarnessRun:
        """Return one exact run."""

    def get_event(self, event_id: str) -> HarnessStoredEvent:
        """Return one exact retained event."""

    def append_event(self, event: HarnessStoredEvent) -> HarnessStoredEvent:
        """Append one already session-bound event."""

    def list_events_page(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
        max_bytes: int = 1024 * 1024,
    ) -> EventTailPage:
        """Return one bounded event page."""


class DataFlowReceiptApplicationError(RuntimeError):
    """Base application error without source or payload content."""


class DataFlowReceiptConflictError(DataFlowReceiptApplicationError):
    """Raised when an immutable receipt identity has conflicting evidence."""


class DataFlowReceiptNotFoundError(
    KeyError,
    DataFlowReceiptApplicationError,
):
    """Raised when one run-bound receipt is not retained."""


@dataclass(frozen=True)
class DataFlowReceiptRecord:
    """One receipt bound to an existing session/run event owner."""

    event_id: str
    session_id: str
    run_id: str
    created_at: str
    receipt: DataFlowReceipt

    @property
    def resource_id(self) -> str:
        """Return the immutable receipt resource identity."""
        return self.receipt.receipt_id

    @property
    def revision(self) -> str:
        """Return the immutable event revision."""
        return self.event_id

    @property
    def sha256(self) -> str:
        """Return the receipt's verified semantic digest."""
        return self.receipt.receipt_id

    def to_dict(self) -> dict[str, Any]:
        """Serialize one bounded application record."""
        return {
            "schema_version": DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION,
            "kind": DATA_FLOW_RECEIPT_EVENT_KIND,
            "event_id": self.event_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "resource_id": self.resource_id,
            "revision": self.revision,
            "sha256": self.sha256,
            "receipt": data_flow_receipt_to_dict(self.receipt),
        }

    @classmethod
    def from_event(cls, event: HarnessStoredEvent) -> DataFlowReceiptRecord:
        """Parse one strict stored receipt event."""
        if event.type != DATA_FLOW_RECEIPT_EVENT_TYPE:
            raise ValueError("event is not a data-flow receipt")
        payload = event.payload
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema_version",
            "kind",
            "receipt_id",
            "receipt",
        }:
            raise ValueError("data-flow receipt event fields are invalid")
        if (
            payload["schema_version"] != DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION
            or payload["kind"] != DATA_FLOW_RECEIPT_EVENT_KIND
        ):
            raise ValueError("data-flow receipt event schema is unsupported")
        raw_receipt = payload["receipt"]
        if not isinstance(raw_receipt, Mapping):
            raise ValueError("data-flow receipt event receipt is invalid")
        receipt = data_flow_receipt_from_dict(raw_receipt)
        if payload["receipt_id"] != receipt.receipt_id:
            raise ValueError("data-flow receipt event identity does not match")
        expected_event_id = _event_id(
            event.session_id, event.run_id, receipt.receipt_id
        )
        if event.id != expected_event_id:
            raise ValueError("data-flow receipt event id does not match")
        return cls(
            event_id=event.id,
            session_id=event.session_id,
            run_id=event.run_id,
            created_at=event.created_at,
            receipt=receipt,
        )


@dataclass(frozen=True)
class DataFlowReceiptPage:
    """Receipt records selected from one bounded owner event page."""

    items: tuple[DataFlowReceiptRecord, ...]
    next_offset: int
    has_more: bool
    scanned_event_count: int
    byte_count: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize one bounded page without a copied cursor payload."""
        return {
            "schema_version": DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION,
            "kind": f"{DATA_FLOW_RECEIPT_EVENT_KIND}.page",
            "items": [item.to_dict() for item in self.items],
            "next_offset": self.next_offset,
            "has_more": self.has_more,
            "scanned_event_count": self.scanned_event_count,
            "byte_count": self.byte_count,
        }


class DataFlowReceiptApplicationService:
    """Persist and read run-bound receipts through the session owner."""

    def __init__(
        self,
        store: ReceiptSessionStore,
        *,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self._store = store
        self._clock = clock
        self._lock = RLock()

    def record(
        self,
        run_id: str,
        receipt: DataFlowReceipt,
    ) -> DataFlowReceiptRecord:
        """Append one immutable receipt or replay its exact existing record."""
        checked = data_flow_receipt_from_dict(data_flow_receipt_to_dict(receipt))
        run = self._store.get_run(run_id)
        event_id = _event_id(run.session_id, run.id, checked.receipt_id)
        with self._lock:
            existing = self._optional_event(event_id)
            if existing is not None:
                record = DataFlowReceiptRecord.from_event(existing)
                if record.run_id != run.id or record.receipt != checked:
                    raise DataFlowReceiptConflictError(
                        "data_flow_receipt_identity_conflict"
                    )
                return record
            event = HarnessStoredEvent(
                id=event_id,
                session_id=run.session_id,
                run_id=run.id,
                type=DATA_FLOW_RECEIPT_EVENT_TYPE,
                message="Recorded bounded data-flow receipt.",
                payload=_event_payload(checked),
                created_at=self._clock(),
                trace_id=run.id,
                span_kind="data_flow",
                span_status=checked.decision.status.value,
            )
            stored = self._store.append_event(event)
            record = DataFlowReceiptRecord.from_event(stored)
            if record.receipt != checked:
                raise DataFlowReceiptConflictError(
                    "data_flow_receipt_changed_during_persistence"
                )
            return record

    def get(
        self,
        run_id: str,
        receipt_id: str,
    ) -> DataFlowReceiptRecord:
        """Read one exact run-bound receipt by deterministic identity."""
        run = self._store.get_run(run_id)
        event_id = _event_id(run.session_id, run.id, receipt_id)
        event = self._optional_event(event_id)
        if event is None:
            raise DataFlowReceiptNotFoundError(receipt_id)
        record = DataFlowReceiptRecord.from_event(event)
        if record.run_id != run.id or record.receipt.receipt_id != receipt_id:
            raise DataFlowReceiptNotFoundError(receipt_id)
        return record

    def list_page(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        max_bytes: int = MAX_DATA_FLOW_RECEIPT_PAGE_BYTES,
    ) -> DataFlowReceiptPage:
        """Filter receipts from one existing byte- and item-bounded event page."""
        if not 1 <= limit <= MAX_DATA_FLOW_RECEIPT_PAGE_ITEMS:
            raise ValueError("data-flow receipt page limit is invalid")
        if not 1024 <= max_bytes <= MAX_DATA_FLOW_RECEIPT_PAGE_BYTES:
            raise ValueError("data-flow receipt page byte limit is invalid")
        run = self._store.get_run(run_id)
        page = self._store.list_events_page(
            run.session_id,
            run_id=run.id,
            offset=offset,
            limit=limit,
            max_bytes=max_bytes,
        )
        records = tuple(
            DataFlowReceiptRecord.from_event(item.event)
            for item in page.items
            if item.event.type == DATA_FLOW_RECEIPT_EVENT_TYPE
        )
        return DataFlowReceiptPage(
            items=records,
            next_offset=page.next_offset,
            has_more=page.has_more,
            scanned_event_count=len(page.items),
            byte_count=page.byte_count,
        )

    def _optional_event(self, event_id: str) -> HarnessStoredEvent | None:
        try:
            return self._store.get_event(event_id)
        except KeyError:
            return None


def _event_payload(receipt: DataFlowReceipt) -> dict[str, Any]:
    return {
        "schema_version": DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION,
        "kind": DATA_FLOW_RECEIPT_EVENT_KIND,
        "receipt_id": receipt.receipt_id,
        "receipt": data_flow_receipt_to_dict(receipt),
    }


def _event_id(session_id: str, run_id: str, receipt_id: str) -> str:
    semantic = f"{session_id}\0{run_id}\0{receipt_id}".encode("utf-8")
    return f"evt_trust_{hashlib.sha256(semantic).hexdigest()}"


__all__ = [
    "DATA_FLOW_RECEIPT_APPLICATION_SCHEMA_VERSION",
    "DATA_FLOW_RECEIPT_EVENT_KIND",
    "DATA_FLOW_RECEIPT_EVENT_TYPE",
    "DataFlowReceiptApplicationError",
    "DataFlowReceiptApplicationService",
    "DataFlowReceiptConflictError",
    "DataFlowReceiptNotFoundError",
    "DataFlowReceiptPage",
    "DataFlowReceiptRecord",
]
