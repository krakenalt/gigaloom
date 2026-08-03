"""Durable, bounded delivery actions owned by the existing sessions tree."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, cast
from uuid import uuid4

from gigaloom.contracts.operational_validation import (
    canonical_json_bytes,
    validate_digest,
    validate_timestamp,
)
from gigaloom.sessions.thread_relay.codec import (
    thread_delivery_receipt_from_dict,
    thread_delivery_receipt_to_dict,
    thread_message_envelope_from_dict,
    thread_message_envelope_to_dict,
)
from gigaloom.sessions.thread_relay.contracts import (
    MAX_THREAD_OUTSTANDING_CHILDREN,
    ThreadDeliveryReceiptV1,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    thread_delivery_receipt_digest,
    thread_locator_digest,
    thread_message_envelope_digest,
)
from gigaloom.sessions.thread_relay.repository_schema import THREAD_DELIVERY_SCHEMA


MAX_THREAD_DELIVERY_PAGE_SIZE = 100
THREAD_DELIVERY_STORE_SCHEMA_VERSION = 1
_ACTIVE_STATUSES = (
    ThreadDeliveryStatus.PENDING.value,
    ThreadDeliveryStatus.ACCEPTED.value,
)
_TERMINAL_STATUSES = frozenset(
    {
        ThreadDeliveryStatus.COMPLETED,
        ThreadDeliveryStatus.FAILED,
        ThreadDeliveryStatus.EXPIRED,
        ThreadDeliveryStatus.CANCELLED,
    }
)
_ALLOWED_TRANSITIONS = {
    ThreadDeliveryStatus.PENDING: frozenset(
        {
            ThreadDeliveryStatus.ACCEPTED,
            ThreadDeliveryStatus.FAILED,
            ThreadDeliveryStatus.EXPIRED,
            ThreadDeliveryStatus.CANCELLED,
        }
    ),
    ThreadDeliveryStatus.ACCEPTED: frozenset(
        {
            ThreadDeliveryStatus.COMPLETED,
            ThreadDeliveryStatus.FAILED,
            ThreadDeliveryStatus.CANCELLED,
        }
    ),
}


class ThreadDeliveryRepositoryError(RuntimeError):
    """Base error for durable Thread Relay actions."""


class ThreadDeliveryConflictError(ThreadDeliveryRepositoryError):
    """Raised when an optimistic or idempotency binding changed."""


class ThreadDeliveryNotFoundError(ThreadDeliveryRepositoryError):
    """Raised when a delivery identity does not exist."""


class ThreadDeliveryCapacityError(ThreadDeliveryRepositoryError):
    """Raised when the bounded outstanding-child limit is reached."""


class ThreadDeliveryIntegrityError(ThreadDeliveryRepositoryError):
    """Raised when persisted immutable delivery evidence is invalid."""


@dataclass(frozen=True, slots=True)
class ThreadDeliveryRecord:
    """One immutable envelope paired with its latest immutable receipt."""

    envelope: ThreadMessageEnvelopeV1
    envelope_digest: str
    receipt: ThreadDeliveryReceiptV1


@dataclass(frozen=True, slots=True)
class ThreadDeliveryReservation:
    """Result of an idempotent delivery reservation."""

    record: ThreadDeliveryRecord
    created: bool


@dataclass(frozen=True, slots=True)
class ThreadDeliveryCursor:
    """Stable newest-first delivery page position."""

    created_at: datetime
    delivery_id: str

    def __post_init__(self) -> None:
        validate_timestamp(self.created_at, field_name="thread delivery cursor time")
        if not self.delivery_id:
            raise ValueError("thread delivery cursor id is required")


@dataclass(frozen=True, slots=True)
class ThreadDeliveryPage:
    """One bounded incoming or outgoing delivery page."""

    items: tuple[ThreadDeliveryRecord, ...]
    next_cursor: ThreadDeliveryCursor | None
    has_more: bool


class ThreadDeliveryRepository:
    """Persist delivery actions without duplicating target transcripts."""

    def __init__(self, data_dir: str | Path) -> None:
        sessions_dir = Path(data_dir).expanduser() / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = sessions_dir / "thread_relay.sqlite3"
        self._initialize()

    def reserve(
        self,
        envelope: ThreadMessageEnvelopeV1,
        *,
        content_digest: str,
        observed_target_revision: str,
        now: datetime,
    ) -> ThreadDeliveryReservation:
        """Reserve one exact revision-bound action or replay its identity."""
        validate_digest(content_digest, field_name="thread delivery content digest")
        validate_timestamp(now, field_name="thread delivery reservation time")
        if envelope.expires_at <= now:
            raise ThreadDeliveryConflictError("thread delivery envelope is expired")
        if envelope.expected_target_revision != observed_target_revision:
            raise ThreadDeliveryConflictError("thread target revision is stale")
        envelope_digest = thread_message_envelope_digest(envelope)
        idempotency_hash = _idempotency_hash(envelope.idempotency_key)
        source_digest = (
            thread_locator_digest(envelope.source_locator)
            if envelope.source_locator is not None
            else None
        )
        with self._connect() as connection, _transaction(connection):
            existing = connection.execute(
                """
                SELECT delivery_id, envelope_digest, content_digest
                FROM thread_deliveries
                WHERE actor_binding = ? AND project_binding = ?
                  AND idempotency_hash = ?
                """,
                (
                    envelope.actor_binding,
                    envelope.project_binding,
                    idempotency_hash,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["envelope_digest"]) != envelope_digest
                    or str(existing["content_digest"]) != content_digest
                ):
                    raise ThreadDeliveryConflictError(
                        "thread idempotency key is bound to another delivery"
                    )
                return ThreadDeliveryReservation(
                    self._record(connection, str(existing["delivery_id"])),
                    created=False,
                )
            if source_digest is not None:
                outstanding = self._outstanding_count(connection, source_digest)
                if outstanding >= MAX_THREAD_OUTSTANDING_CHILDREN:
                    raise ThreadDeliveryCapacityError(
                        "thread outstanding child delivery limit reached"
                    )
            delivery_id = f"delivery_{uuid4().hex}"
            receipt = ThreadDeliveryReceiptV1(
                delivery_id=delivery_id,
                source_identity=envelope.source_locator,
                target_identity=envelope.target_locator,
                action=envelope.intent,
                status=ThreadDeliveryStatus.PENDING,
                created_at=now,
                accepted_at=None,
                completed_at=None,
                run_ref=None,
                job_ref=None,
                turn_ref=None,
                content_digest=content_digest,
                capability_revision=envelope.target_locator.capability_revision,
                terminal_reason=None,
            )
            connection.execute(
                """
                INSERT INTO thread_deliveries (
                    delivery_id, actor_binding, project_binding,
                    source_digest, target_digest, idempotency_hash,
                    envelope_digest, envelope_json, content_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    envelope.actor_binding,
                    envelope.project_binding,
                    source_digest,
                    thread_locator_digest(envelope.target_locator),
                    idempotency_hash,
                    envelope_digest,
                    _wire_json(thread_message_envelope_to_dict(envelope)),
                    content_digest,
                    now.isoformat(),
                ),
            )
            self._insert_receipt(connection, receipt)
            return ThreadDeliveryReservation(
                ThreadDeliveryRecord(envelope, envelope_digest, receipt),
                created=True,
            )

    def get(self, delivery_id: str) -> ThreadDeliveryRecord:
        """Return one delivery with strict digest verification."""
        with self._connect() as connection:
            return self._record(connection, delivery_id)

    def receipts(self, delivery_id: str) -> tuple[ThreadDeliveryReceiptV1, ...]:
        """Return the bounded immutable transition history for one delivery."""
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM thread_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if exists is None:
                raise ThreadDeliveryNotFoundError(delivery_id)
            rows = connection.execute(
                """
                SELECT receipt_json, receipt_digest
                FROM thread_delivery_receipts
                WHERE delivery_id = ? ORDER BY sequence
                """,
                (delivery_id,),
            ).fetchall()
        return tuple(self._receipt_from_row(row) for row in rows)

    def transition(
        self,
        delivery_id: str,
        status: ThreadDeliveryStatus,
        *,
        expected_status: ThreadDeliveryStatus,
        now: datetime,
        run_ref: str | None = None,
        job_ref: str | None = None,
        turn_ref: str | None = None,
        terminal_reason: str | None = None,
    ) -> ThreadDeliveryRecord:
        """Append one optimistic immutable delivery receipt transition."""
        validate_timestamp(now, field_name="thread delivery transition time")
        if not isinstance(status, ThreadDeliveryStatus) or not isinstance(
            expected_status, ThreadDeliveryStatus
        ):
            raise ValueError("thread delivery status is invalid")
        with self._connect() as connection, _transaction(connection):
            current = self._record(connection, delivery_id)
            if current.receipt.status is not expected_status:
                raise ThreadDeliveryConflictError(
                    "thread delivery status changed; resnapshot required"
                )
            if status not in _ALLOWED_TRANSITIONS.get(expected_status, frozenset()):
                raise ThreadDeliveryConflictError(
                    "thread delivery state transition is invalid"
                )
            accepted_at = current.receipt.accepted_at
            completed_at = current.receipt.completed_at
            if status is ThreadDeliveryStatus.ACCEPTED:
                accepted_at = now
            elif status in _TERMINAL_STATUSES:
                completed_at = now
            receipt = replace(
                current.receipt,
                status=status,
                accepted_at=accepted_at,
                completed_at=completed_at,
                run_ref=run_ref if run_ref is not None else current.receipt.run_ref,
                job_ref=job_ref if job_ref is not None else current.receipt.job_ref,
                turn_ref=turn_ref if turn_ref is not None else current.receipt.turn_ref,
                terminal_reason=terminal_reason,
            )
            self._insert_receipt(connection, receipt)
            return ThreadDeliveryRecord(
                current.envelope,
                current.envelope_digest,
                receipt,
            )

    def expire_due(
        self,
        *,
        now: datetime,
        limit: int = MAX_THREAD_DELIVERY_PAGE_SIZE,
        actor_binding: str | None = None,
        project_binding: str | None = None,
    ) -> tuple[ThreadDeliveryRecord, ...]:
        """Expire pending deliveries whose required TTL elapsed."""
        validate_timestamp(now, field_name="thread delivery expiry time")
        _validate_page_limit(limit)
        clauses = ["r.status = ?"]
        params: list[object] = [ThreadDeliveryStatus.PENDING.value]
        if actor_binding is not None:
            clauses.append("d.actor_binding = ?")
            params.append(actor_binding)
        if project_binding is not None:
            clauses.append("d.project_binding = ?")
            params.append(project_binding)
        params.append(limit)
        expired: list[ThreadDeliveryRecord] = []
        with self._connect() as connection:
            rows = connection.execute(
                _LATEST_DELIVERIES_SQL
                + f" WHERE {' AND '.join(clauses)}"
                + " ORDER BY d.created_at, d.delivery_id LIMIT ?",
                tuple(params),
            ).fetchall()
        for row in rows:
            record = self._record_from_joined_row(row)
            if record.envelope.expires_at <= now:
                expired.append(
                    self.transition(
                        record.receipt.delivery_id,
                        ThreadDeliveryStatus.EXPIRED,
                        expected_status=ThreadDeliveryStatus.PENDING,
                        now=now,
                        terminal_reason="delivery_ttl_expired",
                    )
                )
        return tuple(expired)

    def recover_interrupted(
        self,
        *,
        now: datetime,
        limit: int = MAX_THREAD_DELIVERY_PAGE_SIZE,
    ) -> tuple[ThreadDeliveryRecord, ...]:
        """Fail closed accepted deliveries whose outcome is ambiguous after restart."""
        validate_timestamp(now, field_name="thread delivery recovery time")
        _validate_page_limit(limit)
        with self._connect() as connection:
            rows = connection.execute(
                _LATEST_DELIVERIES_SQL
                + " WHERE r.status = ? ORDER BY d.created_at, d.delivery_id LIMIT ?",
                (ThreadDeliveryStatus.ACCEPTED.value, limit),
            ).fetchall()
        recovered: list[ThreadDeliveryRecord] = []
        for row in rows:
            delivery_id = str(row["delivery_id"])
            try:
                recovered.append(
                    self.transition(
                        delivery_id,
                        ThreadDeliveryStatus.FAILED,
                        expected_status=ThreadDeliveryStatus.ACCEPTED,
                        now=now,
                        terminal_reason="restart_outcome_ambiguous",
                    )
                )
            except ThreadDeliveryConflictError:
                continue
        return tuple(recovered)

    def list_for_thread(
        self,
        locator: ThreadLocatorV1,
        *,
        direction: str,
        cursor: ThreadDeliveryCursor | None = None,
        limit: int = 50,
    ) -> ThreadDeliveryPage:
        """Return one bounded newest-first incoming or outgoing page."""
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("thread delivery direction is invalid")
        _validate_page_limit(limit)
        column = "target_digest" if direction == "incoming" else "source_digest"
        params: list[object] = [thread_locator_digest(locator)]
        cursor_clause = ""
        if cursor is not None:
            cursor_clause = (
                " AND (d.created_at < ? OR (d.created_at = ? AND d.delivery_id < ?))"
            )
            encoded_time = cursor.created_at.isoformat()
            params.extend((encoded_time, encoded_time, cursor.delivery_id))
        params.append(limit + 1)
        with self._connect() as connection:
            rows = connection.execute(
                _LATEST_DELIVERIES_SQL
                + f" WHERE d.{column} = ?{cursor_clause}"
                + " ORDER BY d.created_at DESC, d.delivery_id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(self._record_from_joined_row(row) for row in selected)
        next_cursor = (
            ThreadDeliveryCursor(
                items[-1].receipt.created_at,
                items[-1].receipt.delivery_id,
            )
            if has_more and items
            else None
        )
        return ThreadDeliveryPage(items, next_cursor, has_more)

    def _record(
        self,
        connection: sqlite3.Connection,
        delivery_id: str,
    ) -> ThreadDeliveryRecord:
        row = connection.execute(
            _LATEST_DELIVERIES_SQL + " WHERE d.delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        if row is None:
            raise ThreadDeliveryNotFoundError(delivery_id)
        return self._record_from_joined_row(row)

    def _record_from_joined_row(self, row: sqlite3.Row) -> ThreadDeliveryRecord:
        envelope = thread_message_envelope_from_dict(
            _json_mapping(str(row["envelope_json"]))
        )
        envelope_digest = str(row["envelope_digest"])
        if envelope_digest != thread_message_envelope_digest(envelope):
            raise ThreadDeliveryIntegrityError("thread envelope digest mismatch")
        receipt = self._receipt_from_row(row)
        if receipt.delivery_id != str(row["delivery_id"]):
            raise ThreadDeliveryIntegrityError("thread receipt identity mismatch")
        if receipt.content_digest != str(row["content_digest"]):
            raise ThreadDeliveryIntegrityError("thread content digest mismatch")
        return ThreadDeliveryRecord(envelope, envelope_digest, receipt)

    def _receipt_from_row(self, row: sqlite3.Row) -> ThreadDeliveryReceiptV1:
        receipt = thread_delivery_receipt_from_dict(
            _json_mapping(str(row["receipt_json"]))
        )
        if str(row["receipt_digest"]) != thread_delivery_receipt_digest(receipt):
            raise ThreadDeliveryIntegrityError("thread receipt digest mismatch")
        return receipt

    def _insert_receipt(
        self,
        connection: sqlite3.Connection,
        receipt: ThreadDeliveryReceiptV1,
    ) -> None:
        connection.execute(
            """
            INSERT INTO thread_delivery_receipts (
                delivery_id, status, receipt_digest, receipt_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                receipt.delivery_id,
                receipt.status.value,
                thread_delivery_receipt_digest(receipt),
                _wire_json(thread_delivery_receipt_to_dict(receipt)),
                (
                    receipt.completed_at or receipt.accepted_at or receipt.created_at
                ).isoformat(),
            ),
        )

    def _outstanding_count(
        self,
        connection: sqlite3.Connection,
        source_digest: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM thread_deliveries d
            JOIN thread_delivery_receipts r
              ON r.sequence = (
                  SELECT MAX(latest.sequence)
                  FROM thread_delivery_receipts latest
                  WHERE latest.delivery_id = d.delivery_id
              )
            WHERE d.source_digest = ? AND r.status IN (?, ?)
            """,
            (source_digest, *_ACTIVE_STATUSES),
        ).fetchone()
        return int(row["count"])

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(THREAD_DELIVERY_SCHEMA)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, THREAD_DELIVERY_STORE_SCHEMA_VERSION}:
                raise ThreadDeliveryIntegrityError(
                    "unsupported thread delivery store schema"
                )
            if version == 0:
                connection.execute(
                    f"PRAGMA user_version = {THREAD_DELIVERY_STORE_SCHEMA_VERSION}"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


_LATEST_DELIVERIES_SQL = """
    SELECT d.*, r.receipt_json, r.receipt_digest, r.status
    FROM thread_deliveries d
    JOIN thread_delivery_receipts r
      ON r.sequence = (
          SELECT MAX(latest.sequence)
          FROM thread_delivery_receipts latest
          WHERE latest.delivery_id = d.delivery_id
      )
"""


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _idempotency_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_page_limit(limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_THREAD_DELIVERY_PAGE_SIZE
    ):
        raise ValueError(f"limit must be between 1 and {MAX_THREAD_DELIVERY_PAGE_SIZE}")
    return limit


def _wire_json(value: Mapping[str, Any]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _json_mapping(value: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ThreadDeliveryIntegrityError("thread delivery JSON is invalid") from error
    if not isinstance(parsed, Mapping):
        raise ThreadDeliveryIntegrityError("thread delivery JSON must be an object")
    return cast(Mapping[str, Any], parsed)
