"""Store worker lifecycle and maintenance state."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from gigaloom.runtime.db.transactions import transaction as _transaction
from gigaloom.runtime.models import (
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    RuntimeWorker,
    TERMINAL_ATTEMPT_STATUSES,
)
from gigaloom.runtime.repositories.base import RuntimeRepository
from gigaloom.runtime.repositories.errors import (
    AttemptNotFoundError,
    ConcurrentUpdateError,
    InvalidStateTransitionError,
)
from gigaloom.runtime.repositories.records import (
    _attempt_from_row,
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
            row = connection.execute(
                """
                SELECT MIN(deadline) FROM (
                    SELECT MIN(available_at) AS deadline FROM jobs
                    WHERE status IN (?, ?) AND cancel_requested_at IS NULL
                      AND available_at > ?
                    UNION ALL
                    SELECT MIN(leased_until) AS deadline FROM job_attempts
                    WHERE status IN (?, ?, ?) AND leased_until > ?
                    UNION ALL
                    SELECT MIN(next_run_at) AS deadline FROM schedule_states
                    WHERE enabled = 1 AND status = 'active' AND next_run_at > ?
                )
                """,
                (
                    JobStatus.QUEUED.value,
                    JobStatus.RETRY_WAIT.value,
                    now.isoformat(),
                    JobAttemptStatus.CLAIMED.value,
                    JobAttemptStatus.STARTING.value,
                    JobAttemptStatus.RUNNING.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            ).fetchone()
        deadlines: list[float] = []
        if row is not None and row[0] is not None:
            try:
                deadline = datetime.fromisoformat(str(row[0]))
            except ValueError:
                pass
            else:
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                deadlines.append(max((deadline - now).total_seconds(), 0.0))
        return min((maximum, *deadlines))

    def wake_workers(self) -> int:
        """Best-effort signal all local workers after coordination state changes."""
        from gigaloom.runtime.wakeup import signal_workers

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

    def heartbeat_worker_attempt(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_seconds: float,
        minimum_interval_seconds: float = 0.0,
    ) -> JobAttempt:
        """Renew an owned attempt lease and worker heartbeat atomically."""
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        lease_duration = max(float(lease_seconds), 1.0)
        minimum_interval = max(float(minimum_interval_seconds), 0.0)
        with self._connect() as connection, _transaction(connection):
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise AttemptNotFoundError(attempt_id)
            current = _attempt_from_row(row)
            if current.lease_owner != worker_id:
                raise ConcurrentUpdateError(
                    f"attempt {attempt_id} lease is not owned by worker {worker_id}"
                )
            if current.status in TERMINAL_ATTEMPT_STATUSES:
                raise InvalidStateTransitionError(
                    f"terminal attempt {attempt_id} cannot be heartbeated"
                )
            if _heartbeat_is_fresh(
                current,
                now=now,
                minimum_interval_seconds=minimum_interval,
            ):
                return current
            leased_until = (now + timedelta(seconds=lease_duration)).isoformat()
            connection.execute(
                """
                UPDATE job_attempts
                SET heartbeat_at = ?, leased_until = ?, updated_at = ?,
                    version = version + 1
                WHERE id = ? AND lease_owner = ? AND version = ?
                  AND status NOT IN (?, ?, ?, ?)
                """,
                (
                    now_text,
                    leased_until,
                    now_text,
                    attempt_id,
                    worker_id,
                    current.version,
                    JobAttemptStatus.SUCCEEDED.value,
                    JobAttemptStatus.FAILED.value,
                    JobAttemptStatus.CANCELED.value,
                    JobAttemptStatus.INTERRUPTED.value,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(
                    f"attempt {attempt_id} changed while renewing its lease"
                )
            connection.execute(
                """
                UPDATE workers
                SET heartbeat_at = ?, status = 'online'
                WHERE id = ?
                """,
                (now_text, worker_id),
            )
            updated = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return _attempt_from_row(updated)

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


def _heartbeat_is_fresh(
    attempt: JobAttempt,
    *,
    now: datetime,
    minimum_interval_seconds: float,
) -> bool:
    if minimum_interval_seconds <= 0 or attempt.heartbeat_at is None:
        return False
    try:
        heartbeat_at = datetime.fromisoformat(attempt.heartbeat_at)
        leased_until = datetime.fromisoformat(attempt.leased_until or "")
    except ValueError:
        return False
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
    if leased_until.tzinfo is None:
        leased_until = leased_until.replace(tzinfo=timezone.utc)
    age = max((now - heartbeat_at).total_seconds(), 0.0)
    return age < minimum_interval_seconds and leased_until > now
