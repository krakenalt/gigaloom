"""Store job attempts and lease state behind the runtime facade."""

from __future__ import annotations

from typing import Any

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    JobAttempt,
    JobAttemptStatus,
    JobStatus,
    RuntimeJob,
    TERMINAL_ATTEMPT_STATUSES,
    TERMINAL_JOB_STATUSES,
    parse_attempt_status,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.errors import (
    AttemptNotFoundError,
    ConcurrentUpdateError,
    InvalidStateTransitionError,
    JobNotFoundError,
)
from gpt2giga_harness.runtime.repositories.records import (
    _attempt_from_row,
    _future_time,
    _job_from_row,
    _new_id,
    _optional_text,
    _required_text,
    _retry_safe,
    _safe_optional_text,
    _utc_now,
)


class AttemptsRepository(RuntimeRepository):
    """Store job attempts and lease state behind the runtime facade."""

    def create_attempt(
        self,
        job_id: str,
        *,
        run_id: str,
        status: JobAttemptStatus | str = JobAttemptStatus.CLAIMED,
        lease_owner: str | None = None,
        leased_until: str | None = None,
        retry_reason: str | None = None,
        idempotency_class: str = "unknown",
    ) -> JobAttempt:
        """Create the next attempt and bind it to a distinct HarnessRun."""
        target = parse_attempt_status(status)
        run_id = _required_text(run_id, "run_id")
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job_row is None:
                raise JobNotFoundError(job_id)
            job = _job_from_row(job_row)
            if job.status in TERMINAL_JOB_STATUSES:
                raise InvalidStateTransitionError(
                    f"cannot create an attempt for terminal job {job_id}"
                )
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM job_attempts WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            attempt_number = int(row[0]) + 1
            if attempt_number > job.max_attempts:
                raise InvalidStateTransitionError(
                    f"job {job_id} exhausted its {job.max_attempts} attempts"
                )
            attempt_id = _new_id("attempt")
            connection.execute(
                """
                INSERT INTO job_attempts (
                    id, job_id, attempt_number, status, run_id, lease_owner,
                    leased_until, retry_reason, idempotency_class, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    job_id,
                    attempt_number,
                    target.value,
                    run_id,
                    _optional_text(lease_owner),
                    leased_until,
                    _safe_optional_text(retry_reason),
                    _required_text(idempotency_class, "idempotency_class"),
                    now,
                    now,
                ),
            )
            if job.status is not JobStatus.RUNNING:
                connection.execute(
                    """
                    UPDATE jobs SET status = ?, terminal_at = NULL,
                        updated_at = ?, version = version + 1 WHERE id = ?
                    """,
                    (JobStatus.RUNNING.value, now, job_id),
                )
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return _attempt_from_row(attempt_row)

    def get_attempt(self, attempt_id: str) -> JobAttempt:
        """Return one attempt."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptNotFoundError(attempt_id)
        return _attempt_from_row(row)

    def list_attempts(self, job_id: str | None = None) -> tuple[JobAttempt, ...]:
        """List attempts globally or for one job."""
        query = "SELECT * FROM job_attempts"
        params: tuple[Any, ...] = ()
        if job_id is not None:
            query += " WHERE job_id = ?"
            params = (job_id,)
        query += " ORDER BY created_at, attempt_number, id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def heartbeat_attempt(
        self, attempt_id: str, *, worker_id: str, lease_seconds: float
    ) -> JobAttempt:
        """Renew an owned attempt lease."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE job_attempts
                SET heartbeat_at = ?, leased_until = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND lease_owner = ? AND status NOT IN (?, ?, ?, ?)
                """,
                (
                    now,
                    _future_time(max(lease_seconds, 1.0)),
                    now,
                    attempt_id,
                    worker_id,
                    JobAttemptStatus.SUCCEEDED.value,
                    JobAttemptStatus.FAILED.value,
                    JobAttemptStatus.CANCELED.value,
                    JobAttemptStatus.INTERRUPTED.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptNotFoundError(attempt_id)
        return _attempt_from_row(row)

    def update_attempt_process(
        self, attempt_id: str, *, process_id: int, process_group_id: int | None
    ) -> JobAttempt:
        """Persist redacted process ownership metadata for cancellation/audit."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE job_attempts SET process_id = ?, process_group_id = ?,
                    updated_at = ?, version = version + 1 WHERE id = ?
                """,
                (process_id, process_group_id, _utc_now(), attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptNotFoundError(attempt_id)
        return _attempt_from_row(row)

    def transition_attempt(
        self,
        attempt_id: str,
        status: JobAttemptStatus | str,
        *,
        expected_status: JobAttemptStatus | str | None = None,
        process_id: int | None = None,
        error_summary: str | None = None,
    ) -> JobAttempt:
        """Atomically transition one concrete attempt."""
        target = parse_attempt_status(status)
        expected = parse_attempt_status(expected_status) if expected_status else None
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise AttemptNotFoundError(attempt_id)
            current = _attempt_from_row(row)
            if expected is not None and current.status is not expected:
                raise ConcurrentUpdateError(
                    f"attempt {attempt_id} is {current.status.value}, expected {expected.value}"
                )
            if current.status is target:
                return current
            if current.status in TERMINAL_ATTEMPT_STATUSES:
                raise InvalidStateTransitionError(
                    f"terminal attempt {attempt_id} cannot transition to {target.value}"
                )
            started_at = current.started_at or (
                now
                if target in {JobAttemptStatus.STARTING, JobAttemptStatus.RUNNING}
                else None
            )
            finished_at = now if target in TERMINAL_ATTEMPT_STATUSES else None
            next_version = current.version + 1
            connection.execute(
                """
                UPDATE job_attempts
                SET status = ?, started_at = ?, finished_at = ?,
                    process_id = COALESCE(?, process_id), error_summary = ?,
                    updated_at = ?, version = ?
                WHERE id = ? AND version = ?
                """,
                (
                    target.value,
                    started_at,
                    finished_at,
                    process_id,
                    _safe_optional_text(error_summary),
                    now,
                    next_version,
                    attempt_id,
                    current.version,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(
                    f"attempt {attempt_id} changed concurrently"
                )
            updated = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return _attempt_from_row(updated)

    def set_attempt_idempotency_class(
        self, attempt_id: str, idempotency_class: str
    ) -> JobAttempt:
        """Record the retry safety class resolved from the immutable payload."""
        value = _required_text(idempotency_class, "idempotency_class")
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                "UPDATE job_attempts SET idempotency_class = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                (value, _utc_now(), attempt_id),
            )
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise AttemptNotFoundError(attempt_id)
        return _attempt_from_row(row)

    def finish_attempt(
        self,
        attempt_id: str,
        status: JobAttemptStatus | str,
        *,
        error_summary: str | None = None,
        retry_delay_seconds: float | None = None,
        sync_terminal_run: bool = True,
    ) -> tuple[JobAttempt, RuntimeJob]:
        """Finish an attempt and atomically retry or terminate its logical job."""
        target = parse_attempt_status(status)
        if target not in TERMINAL_ATTEMPT_STATUSES:
            raise ValueError("finish_attempt requires a terminal attempt status")
        now = _utc_now()
        safe_error = _safe_optional_text(error_summary)
        with self._connect() as connection, _transaction(connection):
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if attempt_row is None:
                raise AttemptNotFoundError(attempt_id)
            attempt = _attempt_from_row(attempt_row)
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (attempt.job_id,)
            ).fetchone()
            job = _job_from_row(job_row)
            if attempt.status not in TERMINAL_ATTEMPT_STATUSES:
                connection.execute(
                    """
                    UPDATE job_attempts SET status = ?, finished_at = ?,
                        error_summary = ?, updated_at = ?, version = version + 1
                    WHERE id = ?
                    """,
                    (target.value, now, safe_error, now, attempt.id),
                )
            retryable = (
                target in {JobAttemptStatus.FAILED, JobAttemptStatus.INTERRUPTED}
                and retry_delay_seconds is not None
                and attempt.attempt_number < job.max_attempts
                and _retry_safe(attempt.idempotency_class)
                and job.cancel_requested_at is None
            )
            if retryable:
                job_status = JobStatus.RETRY_WAIT
                available_at = _future_time(max(retry_delay_seconds, 0.0))
                terminal_at = None
            else:
                job_status = {
                    JobAttemptStatus.SUCCEEDED: JobStatus.SUCCEEDED,
                    JobAttemptStatus.CANCELED: JobStatus.CANCELED,
                }.get(target, JobStatus.FAILED)
                available_at = job.available_at
                terminal_at = now
            connection.execute(
                """
                UPDATE jobs SET status = ?, available_at = ?, terminal_at = ?,
                    error_summary = ?, updated_at = ?, version = version + 1
                WHERE id = ?
                """,
                (
                    job_status.value,
                    available_at,
                    terminal_at,
                    safe_error,
                    now,
                    job.id,
                ),
            )
            updated_attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt.id,)
            ).fetchone()
            updated_job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if job_status in TERMINAL_JOB_STATUSES and sync_terminal_run:
                self._enqueue_terminal_sync(
                    connection,
                    job_id=job.id,
                    status=job_status,
                    version=int(updated_job_row["version"]),
                    session_id=job.session_id,
                    attempt=_attempt_from_row(updated_attempt_row),
                )
        return _attempt_from_row(updated_attempt_row), _job_from_row(updated_job_row)

    def recover_expired_attempts(
        self, *, retry_delay_seconds: float = 1.0
    ) -> tuple[JobAttempt, ...]:
        """Mark expired leases interrupted and requeue only retry-safe work."""
        now = _utc_now()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM job_attempts
                WHERE status IN (?, ?, ?) AND leased_until IS NOT NULL
                  AND leased_until < ?
                ORDER BY leased_until, id
                """,
                (
                    JobAttemptStatus.CLAIMED.value,
                    JobAttemptStatus.STARTING.value,
                    JobAttemptStatus.RUNNING.value,
                    now,
                ),
            ).fetchall()
        recovered: list[JobAttempt] = []
        for row in rows:
            attempt = _attempt_from_row(row)
            updated, _ = self.finish_attempt(
                attempt.id,
                JobAttemptStatus.INTERRUPTED,
                error_summary="worker lease expired; process adoption was not attempted",
                retry_delay_seconds=retry_delay_seconds,
            )
            recovered.append(updated)
        return tuple(recovered)
