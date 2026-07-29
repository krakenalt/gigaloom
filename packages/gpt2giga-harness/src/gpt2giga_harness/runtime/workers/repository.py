"""Store worker lifecycle and maintenance state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    JobAttemptStatus,
    JobStatus,
    RuntimeWorker,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.records import (
    _safe_json,
    _utc_now,
    _worker_from_row,
)


class WorkersRepository(RuntimeRepository):
    """Store worker lifecycle and maintenance state."""

    def requeue_due_jobs(self) -> int:
        """Move due retry-wait jobs back to the claimable queue."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, version = version + 1
                WHERE status = ? AND available_at <= ? AND cancel_requested_at IS NULL
                """,
                (JobStatus.QUEUED.value, now, JobStatus.RETRY_WAIT.value, now),
            )
            return int(connection.execute("SELECT changes()").fetchone()[0])

    def next_worker_maintenance_delay(self, maximum_seconds: float) -> float:
        """Bound an idle wait by the next queued, retry, lease, or schedule deadline."""
        maximum = max(float(maximum_seconds), 0.0)
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            rows = (
                connection.execute(
                    """
                    SELECT MIN(available_at) FROM jobs
                    WHERE status IN (?, ?) AND cancel_requested_at IS NULL
                      AND available_at > ?
                    """,
                    (
                        JobStatus.QUEUED.value,
                        JobStatus.RETRY_WAIT.value,
                        now.isoformat(),
                    ),
                ).fetchone(),
                connection.execute(
                    """
                    SELECT MIN(leased_until) FROM job_attempts
                    WHERE status IN (?, ?, ?) AND leased_until > ?
                    """,
                    (
                        JobAttemptStatus.CLAIMED.value,
                        JobAttemptStatus.STARTING.value,
                        JobAttemptStatus.RUNNING.value,
                        now.isoformat(),
                    ),
                ).fetchone(),
                connection.execute(
                    """
                    SELECT MIN(next_run_at) FROM schedule_states
                    WHERE enabled = 1 AND status = 'active' AND next_run_at > ?
                    """,
                    (now.isoformat(),),
                ).fetchone(),
            )
        deadlines: list[float] = []
        for row in rows:
            if row is None or row[0] is None:
                continue
            try:
                deadline = datetime.fromisoformat(str(row[0]))
            except ValueError:
                continue
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            deadlines.append(max((deadline - now).total_seconds(), 0.0))
        return min((maximum, *deadlines))

    def wake_workers(self) -> int:
        """Best-effort signal all local workers after coordination state changes."""
        from gpt2giga_harness.runtime.wakeup import signal_workers

        return signal_workers(self.data_dir)

    def register_worker(
        self,
        *,
        worker_id: str,
        process_id: int,
        hostname: str,
        capability_fingerprint: Mapping[str, Any],
    ) -> RuntimeWorker:
        """Register or refresh a durable worker identity."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                INSERT INTO workers (
                    id, process_id, hostname, status, started_at, heartbeat_at,
                    stopped_at, capability_fingerprint_json
                ) VALUES (?, ?, ?, 'online', ?, ?, NULL, ?)
                ON CONFLICT(id) DO UPDATE SET process_id = excluded.process_id,
                    hostname = excluded.hostname, status = 'online',
                    heartbeat_at = excluded.heartbeat_at, stopped_at = NULL,
                    capability_fingerprint_json = excluded.capability_fingerprint_json
                """,
                (
                    worker_id,
                    process_id,
                    hostname,
                    now,
                    now,
                    _safe_json(capability_fingerprint),
                ),
            )
            row = connection.execute(
                "SELECT * FROM workers WHERE id = ?", (worker_id,)
            ).fetchone()
        return _worker_from_row(row)

    def heartbeat_worker(self, worker_id: str) -> None:
        """Refresh one worker liveness timestamp."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                "UPDATE workers SET heartbeat_at = ?, status = 'online' WHERE id = ?",
                (_utc_now(), worker_id),
            )

    def stop_worker(self, worker_id: str) -> None:
        """Mark one worker as cleanly stopped."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                "UPDATE workers SET status = 'stopped', stopped_at = ?, heartbeat_at = ? WHERE id = ?",
                (now, now, worker_id),
            )

    def list_workers(self) -> tuple[RuntimeWorker, ...]:
        """List durable worker records newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workers ORDER BY heartbeat_at DESC, id"
            ).fetchall()
        return tuple(_worker_from_row(row) for row in rows)
