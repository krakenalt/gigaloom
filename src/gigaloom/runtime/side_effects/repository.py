"""Store durable idempotent side-effect reservations."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from gigaloom.runtime.db.transactions import transaction as _transaction
from gigaloom.runtime.models import (
    SideEffectRecord,
    SideEffectReservation,
    SideEffectStatus,
    TERMINAL_ATTEMPT_STATUSES,
)
from gigaloom.runtime.repositories.base import RuntimeRepository
from gigaloom.runtime.repositories.errors import (
    AttemptNotFoundError,
    ConcurrentUpdateError,
    InvalidStateTransitionError,
    SideEffectBlockedError,
    SideEffectConflictError,
    SideEffectNotFoundError,
)
from gigaloom.runtime.repositories.records import (
    _attempt_from_row,
    _canonical_json,
    _job_from_row,
    _mapping_hash,
    _new_id,
    _opaque_token_hash,
    _required_text,
    _safe_mapping,
    _side_effect_from_row,
    _utc_now,
)
from gigaloom.sessions.contracts import redact_for_storage


class SideEffectsRepository(RuntimeRepository):
    """Store durable idempotent side-effect reservations."""

    def reserve_side_effect(
        self,
        *,
        job_id: str,
        attempt_id: str,
        token: str,
        operation: str,
        intent: Mapping[str, Any],
    ) -> SideEffectReservation:
        """Atomically reserve one opaque token for a Harness-owned side effect."""
        token_hash = _opaque_token_hash(token)
        operation = _required_text(operation, "operation")
        intent_hash = _mapping_hash(intent, "intent")
        now = _utc_now()
        record_id = _new_id("effect")
        with self._connect() as connection, _transaction(connection):
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if attempt_row is None:
                raise AttemptNotFoundError(attempt_id)
            attempt = _attempt_from_row(attempt_row)
            if attempt.job_id != job_id:
                raise SideEffectConflictError(
                    "side-effect attempt does not belong to the logical job"
                )
            if attempt.status in TERMINAL_ATTEMPT_STATUSES:
                raise InvalidStateTransitionError(
                    "terminal attempts cannot reserve side effects"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO harness_side_effects (
                        id, job_id, token_hash, operation, intent_hash, status,
                        owner_attempt_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record_id,
                        job_id,
                        token_hash,
                        operation,
                        intent_hash,
                        SideEffectStatus.RESERVED.value,
                        attempt_id,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM harness_side_effects WHERE id = ?", (record_id,)
                ).fetchone()
                return SideEffectReservation(
                    record=_side_effect_from_row(row), created=True
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM harness_side_effects WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if row is None:
                    raise
                existing = _side_effect_from_row(row)
                if (
                    existing.job_id != job_id
                    or existing.operation != operation
                    or existing.intent_hash != intent_hash
                ):
                    raise SideEffectConflictError(
                        "side-effect token is already bound to different intent"
                    ) from None
                return SideEffectReservation(record=existing, created=False)

    def complete_side_effect(
        self,
        record_id: str,
        *,
        attempt_id: str,
        evidence: Mapping[str, Any],
    ) -> SideEffectRecord:
        """Complete one owned reservation with immutable redacted evidence."""
        safe_evidence = _safe_mapping(evidence, "completion evidence")
        if not safe_evidence:
            raise ValueError("completion evidence is required")
        evidence_hash = _mapping_hash(safe_evidence, "completion evidence")
        evidence_json = _canonical_json(safe_evidence, "completion evidence")
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            row = connection.execute(
                "SELECT * FROM harness_side_effects WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                raise SideEffectNotFoundError(record_id)
            record = _side_effect_from_row(row)
            if record.owner_attempt_id != attempt_id:
                raise SideEffectConflictError(
                    "only the reserving attempt can complete a side effect"
                )
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if attempt_row is None:
                raise AttemptNotFoundError(attempt_id)
            attempt = _attempt_from_row(attempt_row)
            if attempt.job_id != record.job_id:
                raise SideEffectConflictError(
                    "side-effect attempt does not belong to the logical job"
                )
            if record.status is SideEffectStatus.COMPLETED:
                if record.completion_evidence_hash != evidence_hash:
                    raise SideEffectConflictError(
                        "side effect already has different completion evidence"
                    )
                return record
            if attempt.status in TERMINAL_ATTEMPT_STATUSES:
                raise InvalidStateTransitionError(
                    "terminal attempts cannot complete side effects"
                )
            connection.execute(
                """
                UPDATE harness_side_effects
                SET status = ?, completion_evidence_json = ?,
                    completion_evidence_hash = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND status = ? AND owner_attempt_id = ?
                """,
                (
                    SideEffectStatus.COMPLETED.value,
                    evidence_json,
                    evidence_hash,
                    now,
                    now,
                    record_id,
                    SideEffectStatus.RESERVED.value,
                    attempt_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(
                    f"side effect {record_id} changed concurrently"
                )
            updated = connection.execute(
                "SELECT * FROM harness_side_effects WHERE id = ?", (record_id,)
            ).fetchone()
        return _side_effect_from_row(updated)

    def enqueue_side_effect_event(
        self,
        *,
        job_id: str,
        attempt_id: str,
        token: str,
        event_type: str,
        message: str,
        payload: Mapping[str, Any],
    ) -> SideEffectReservation:
        """Atomically enqueue one idempotent Harness-owned runtime event."""
        operation = "runtime.event.enqueue"
        safe_event_type = _required_text(event_type, "event_type")
        safe_message = str(redact_for_storage(_required_text(message, "message")))
        safe_payload = _safe_mapping(payload, "event payload")
        intent = {
            "event_type": safe_event_type,
            "message": safe_message,
            "payload": safe_payload,
        }
        token_hash = _opaque_token_hash(token)
        intent_hash = _mapping_hash(intent, "intent")
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if attempt_row is None:
                raise AttemptNotFoundError(attempt_id)
            attempt = _attempt_from_row(attempt_row)
            if attempt.job_id != job_id:
                raise SideEffectConflictError(
                    "side-effect attempt does not belong to the logical job"
                )
            if attempt.status in TERMINAL_ATTEMPT_STATUSES:
                raise InvalidStateTransitionError(
                    "terminal attempts cannot execute side effects"
                )
            existing_row = connection.execute(
                "SELECT * FROM harness_side_effects WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if existing_row is not None:
                existing = _side_effect_from_row(existing_row)
                if (
                    existing.job_id != job_id
                    or existing.operation != operation
                    or existing.intent_hash != intent_hash
                ):
                    raise SideEffectConflictError(
                        "side-effect token is already bound to different intent"
                    )
                if existing.status is SideEffectStatus.COMPLETED:
                    return SideEffectReservation(record=existing, created=False)
                raise SideEffectBlockedError(
                    "side effect remains reserved by an earlier attempt; "
                    "automatic replay is blocked"
                )

            record_id = _new_id("effect")
            outbox_id = _new_id("outbox")
            completion_evidence = {
                "delivery": "runtime_outbox",
                "event_id": f"evt_{outbox_id}",
                "outbox_id": outbox_id,
            }
            evidence_hash = _mapping_hash(completion_evidence, "completion evidence")
            connection.execute(
                """
                INSERT INTO harness_side_effects (
                    id, job_id, token_hash, operation, intent_hash, status,
                    owner_attempt_id, completion_evidence_json,
                    completion_evidence_hash, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    job_id,
                    token_hash,
                    operation,
                    intent_hash,
                    SideEffectStatus.COMPLETED.value,
                    attempt_id,
                    _canonical_json(completion_evidence, "completion evidence"),
                    evidence_hash,
                    now,
                    now,
                    now,
                ),
            )
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            job = _job_from_row(job_row)
            outbox_payload = {
                "side_effect_id": record_id,
                "session_id": job.session_id,
                "run_id": attempt.run_id,
                "job_id": job.id,
                "attempt_id": attempt.id,
                "event_type": safe_event_type,
                "message": safe_message,
                "event_payload": safe_payload,
            }
            connection.execute(
                """
                INSERT INTO runtime_outbox (
                    id, aggregate_type, aggregate_id, event_type, dedupe_key,
                    payload_json, created_at
                ) VALUES (?, 'side_effect', ?, 'side_effect_event', ?, ?, ?)
                """,
                (
                    outbox_id,
                    record_id,
                    f"side-effect:{record_id}:event",
                    _canonical_json(outbox_payload, "side-effect outbox payload"),
                    now,
                ),
            )
            completed_row = connection.execute(
                "SELECT * FROM harness_side_effects WHERE id = ?", (record_id,)
            ).fetchone()
        return SideEffectReservation(
            record=_side_effect_from_row(completed_row), created=True
        )

    def get_side_effect(self, record_id: str) -> SideEffectRecord:
        """Return one durable side-effect record by public identity."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM harness_side_effects WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise SideEffectNotFoundError(record_id)
        return _side_effect_from_row(row)

    def get_side_effect_for_token(self, token: str) -> SideEffectRecord:
        """Resolve an opaque token without persisting or returning its raw value."""
        token_hash = _opaque_token_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM harness_side_effects WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            raise SideEffectNotFoundError(token_hash)
        return _side_effect_from_row(row)

    def list_side_effects(
        self, job_id: str | None = None
    ) -> tuple[SideEffectRecord, ...]:
        """List durable side effects in stable creation order."""
        with self._connect() as connection:
            if job_id is None:
                rows = connection.execute(
                    "SELECT * FROM harness_side_effects ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM harness_side_effects
                    WHERE job_id = ? ORDER BY created_at, id
                    """,
                    (job_id,),
                ).fetchall()
        return tuple(_side_effect_from_row(row) for row in rows)
