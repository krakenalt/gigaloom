"""Store transactional coordination outbox records."""

from __future__ import annotations

import json
import sqlite3

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    JobAttempt,
    JobStatus,
    RuntimeOutboxEntry,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.records import (
    _new_id,
    _outbox_from_row,
    _required_text,
    _safe_optional_text,
    _utc_now,
)


class OutboxRepository(RuntimeRepository):
    """Store transactional coordination outbox records."""

    def next_trace_sequence(self, trace_id: str) -> int:
        """Allocate one process-safe monotonically increasing trace sequence."""
        trace_id = _required_text(trace_id, "trace_id")
        with self._connect() as connection, _transaction(connection):
            row = connection.execute(
                "SELECT last_sequence FROM trace_sequences WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            sequence = int(row[0]) + 1 if row is not None else 1
            connection.execute(
                """
                INSERT INTO trace_sequences(trace_id, last_sequence) VALUES (?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET last_sequence = excluded.last_sequence
                """,
                (trace_id, sequence),
            )
        return sequence

    def pending_outbox(self, *, limit: int = 100) -> tuple[RuntimeOutboxEntry, ...]:
        """Return unprocessed bridge events."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_outbox WHERE processed_at IS NULL
                ORDER BY created_at, id LIMIT ?
                """,
                (max(0, limit),),
            ).fetchall()
        return tuple(_outbox_from_row(row) for row in rows)

    def mark_outbox_processed(self, entry_id: str) -> None:
        """Mark one bridge event as successfully applied."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE runtime_outbox
                SET processed_at = ?, attempt_count = attempt_count + 1,
                    last_error = NULL
                WHERE id = ? AND processed_at IS NULL
                """,
                (_utc_now(), entry_id),
            )

    def record_outbox_failure(self, entry_id: str, error: str) -> None:
        """Record a redacted recovery failure for a later retry."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE runtime_outbox
                SET attempt_count = attempt_count + 1, last_error = ?
                WHERE id = ? AND processed_at IS NULL
                """,
                (_safe_optional_text(error), entry_id),
            )

    def _enqueue_terminal_sync(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        status: JobStatus,
        version: int,
        session_id: str,
        attempt: JobAttempt | None,
    ) -> None:
        payload = {
            "job_id": job_id,
            "status": status.value,
            "session_id": session_id,
            "attempt_id": attempt.id if attempt is not None else None,
            "run_id": attempt.run_id if attempt is not None else None,
        }
        connection.execute(
            """
            INSERT OR IGNORE INTO runtime_outbox (
                id, aggregate_type, aggregate_id, event_type, dedupe_key,
                payload_json, created_at
            ) VALUES (?, 'job', ?, 'job_terminal', ?, ?, ?)
            """,
            (
                _new_id("outbox"),
                job_id,
                f"job:{job_id}:terminal:{version}",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                _utc_now(),
            ),
        )
