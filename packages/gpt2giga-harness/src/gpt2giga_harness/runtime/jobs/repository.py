"""Store logical jobs behind the runtime facade."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    JobAttemptStatus,
    JobStatus,
    JobSubmission,
    RuntimeJob,
    TERMINAL_JOB_STATUSES,
    parse_job_status,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.errors import (
    ConcurrentUpdateError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    JobNotFoundError,
)
from gpt2giga_harness.runtime.repositories.records import (
    _attempt_from_row,
    _idempotency_hash,
    _job_from_row,
    _new_id,
    _optional_text,
    _required_text,
    _retry_safe,
    _safe_json,
    _safe_optional_text,
    _utc_now,
)


class JobsRepository(RuntimeRepository):
    """Store logical jobs behind the runtime facade."""

    def submit_job(
        self,
        *,
        session_id: str,
        user_message_id: str,
        idempotency_key: str,
        initial_run_id: str | None = None,
        origin: str = "manual",
        project_id: str | None = None,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        schedule_id: str | None = None,
        agent_id: str | None = None,
        max_attempts: int = 1,
        priority: int = 0,
        available_at: str | None = None,
        required_harness_id: str | None = None,
        required_capability_fingerprint: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
        initial_status: JobStatus | str = JobStatus.QUEUED,
    ) -> JobSubmission:
        """Submit one job or return the existing identity-matched job."""
        session_id = _required_text(session_id, "session_id")
        user_message_id = _required_text(user_message_id, "user_message_id")
        origin = _required_text(origin, "origin")
        submit_status = parse_job_status(initial_status)
        if submit_status not in {
            JobStatus.QUEUED,
            JobStatus.WAITING_INPUT,
            JobStatus.WAITING_APPROVAL,
        }:
            raise ValueError(
                "initial job status must be queued, waiting_input, or waiting_approval"
            )
        initial_run_id = _required_text(
            initial_run_id or _new_id("run"), "initial_run_id"
        )
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        key_hash = _idempotency_hash(idempotency_key)
        now = _utc_now()
        job_id = _new_id("job")
        required_fingerprint = required_capability_fingerprint or {}
        values = {
            "id": job_id,
            "origin": origin,
            "idempotency_key_hash": key_hash,
            "status": submit_status.value,
            "session_id": session_id,
            "user_message_id": user_message_id,
            "initial_run_id": initial_run_id,
            "project_id": _optional_text(project_id),
            "workflow_id": _optional_text(workflow_id),
            "workflow_version": _optional_text(workflow_version),
            "schedule_id": _optional_text(schedule_id),
            "agent_id": _optional_text(agent_id),
            "available_at": available_at or now,
            "max_attempts": max_attempts,
            "priority": int(priority),
            "required_harness_id": _optional_text(required_harness_id),
            "required_fingerprint_json": _safe_json(required_fingerprint),
            "required_os": _optional_text(required_fingerprint.get("os")),
            "timeout_seconds": (
                float(timeout_seconds) if timeout_seconds is not None else None
            ),
            "created_at": now,
            "updated_at": now,
        }
        result: JobSubmission
        with self._connect() as connection, _transaction(connection):
            try:
                connection.execute(
                    """
                    INSERT INTO jobs (
                        id, origin, idempotency_key_hash, status, session_id,
                        user_message_id, initial_run_id, project_id, workflow_id, workflow_version,
                        schedule_id, agent_id, available_at, max_attempts, priority,
                        required_harness_id, required_fingerprint_json, required_os,
                        timeout_seconds, created_at, updated_at
                    ) VALUES (
                        :id, :origin, :idempotency_key_hash, :status, :session_id,
                        :user_message_id, :initial_run_id, :project_id, :workflow_id, :workflow_version,
                        :schedule_id, :agent_id, :available_at, :max_attempts,
                        :priority, :required_harness_id, :required_fingerprint_json,
                        :required_os, :timeout_seconds,
                        :created_at, :updated_at
                    )
                    """,
                    values,
                )
                row = connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                result = JobSubmission(job=_job_from_row(row), created=True)
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE origin = ? AND idempotency_key_hash = ?
                    """,
                    (origin, key_hash),
                ).fetchone()
                if row is None:
                    raise
                existing = _job_from_row(row)
                expected_identity = (
                    session_id,
                    user_message_id,
                    _optional_text(project_id),
                    _optional_text(workflow_id),
                    _optional_text(workflow_version),
                    _optional_text(schedule_id),
                    _optional_text(agent_id),
                )
                actual_identity = (
                    existing.session_id,
                    existing.user_message_id,
                    existing.project_id,
                    existing.workflow_id,
                    existing.workflow_version,
                    existing.schedule_id,
                    existing.agent_id,
                )
                if actual_identity != expected_identity:
                    raise IdempotencyConflictError(
                        "idempotency key is already bound to a different job"
                    )
                result = JobSubmission(job=existing, created=False)
        if result.created and result.job.status is JobStatus.QUEUED:
            self.wake_workers()
        return result

    def find_job_by_idempotency(
        self, *, origin: str, idempotency_key: str
    ) -> RuntimeJob | None:
        """Return a job previously submitted with the caller-owned key."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE origin = ? AND idempotency_key_hash = ?",
                (_required_text(origin, "origin"), _idempotency_hash(idempotency_key)),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def get_job(self, job_id: str) -> RuntimeJob:
        """Return one job."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return _job_from_row(row)

    def list_jobs(self) -> tuple[RuntimeJob, ...]:
        """List all jobs in stable creation order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at, id"
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def list_jobs_page(
        self,
        *,
        statuses: tuple[JobStatus | str, ...] = (),
        project_id: str | None = None,
        harness_id: str | None = None,
        cursor: tuple[str, str] | None = None,
        limit: int = 25,
    ) -> tuple[tuple[RuntimeJob, ...], bool]:
        """List a newest-first cursor page without loading task payloads."""
        page_size = max(1, min(int(limit), 100))
        clauses: list[str] = []
        params: list[Any] = []
        parsed_statuses = tuple(parse_job_status(status) for status in statuses)
        if parsed_statuses:
            placeholders = ", ".join("?" for _ in parsed_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(status.value for status in parsed_statuses)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(_required_text(project_id, "project_id"))
        if harness_id is not None:
            clauses.append("required_harness_id = ?")
            params.append(_required_text(harness_id, "harness_id"))
        if cursor is not None:
            created_at, job_id = cursor
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            params.extend((created_at, created_at, job_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(page_size + 1)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs{where} ORDER BY created_at DESC, id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        has_more = len(rows) > page_size
        return (
            tuple(_job_from_row(row) for row in rows[:page_size]),
            has_more,
        )

    def transition_job(
        self,
        job_id: str,
        status: JobStatus | str,
        *,
        expected_status: JobStatus | str | None = None,
        error_summary: str | None = None,
        available_at: str | None = None,
    ) -> RuntimeJob:
        """Atomically transition one logical job and enqueue terminal sync."""
        target = parse_job_status(status)
        expected = parse_job_status(expected_status) if expected_status else None
        now = _utc_now()
        safe_error = _safe_optional_text(error_summary)
        with self._connect() as connection, _transaction(connection):
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            current = _job_from_row(row)
            if expected is not None and current.status is not expected:
                raise ConcurrentUpdateError(
                    f"job {job_id} is {current.status.value}, expected {expected.value}"
                )
            if current.status is target:
                return current
            if current.status in TERMINAL_JOB_STATUSES:
                raise InvalidStateTransitionError(
                    f"terminal job {job_id} cannot transition to {target.value}"
                )
            terminal_at = now if target in TERMINAL_JOB_STATUSES else None
            next_version = current.version + 1
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, available_at = COALESCE(?, available_at),
                    terminal_at = ?, error_summary = ?, updated_at = ?,
                    version = ?
                WHERE id = ? AND version = ?
                """,
                (
                    target.value,
                    available_at,
                    terminal_at,
                    safe_error,
                    now,
                    next_version,
                    job_id,
                    current.version,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(f"job {job_id} changed concurrently")
            if target in TERMINAL_JOB_STATUSES:
                attempt_row = connection.execute(
                    """
                    SELECT * FROM job_attempts
                    WHERE job_id = ? ORDER BY attempt_number DESC LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()
                attempt = _attempt_from_row(attempt_row) if attempt_row else None
                self._enqueue_terminal_sync(
                    connection,
                    job_id=job_id,
                    status=target,
                    version=next_version,
                    session_id=current.session_id,
                    attempt=attempt,
                )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        result = _job_from_row(updated)
        self.wake_workers()
        return result

    def find_job_for_run(self, run_id: str) -> RuntimeJob | None:
        """Return the job owning an initial or attempted run."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT jobs.* FROM jobs
                LEFT JOIN job_attempts ON job_attempts.job_id = jobs.id
                WHERE jobs.initial_run_id = ? OR job_attempts.run_id = ?
                ORDER BY job_attempts.attempt_number DESC LIMIT 1
                """,
                (run_id, run_id),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def link_job_workflow(
        self, job_id: str, *, workflow_id: str, workflow_version: str
    ) -> RuntimeJob:
        """Attach an already-submitted child job to its immutable workflow run."""
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE jobs SET workflow_id = ?, workflow_version = ?,
                    updated_at = ?, version = version + 1
                WHERE id = ? AND (workflow_id IS NULL OR workflow_id = ?)
                """,
                (workflow_id, workflow_version, _utc_now(), job_id, workflow_id),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        job = _job_from_row(row)
        if job.workflow_id != workflow_id:
            raise ConcurrentUpdateError(f"job {job_id} belongs to another workflow")
        return job

    def request_cancel(self, job_id: str) -> RuntimeJob:
        """Persist a cooperative cancellation request for a logical job."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE jobs SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    updated_at = ?, version = version + 1
                WHERE id = ? AND status NOT IN (?, ?, ?)
                """,
                (
                    now,
                    now,
                    job_id,
                    JobStatus.SUCCEEDED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELED.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return _job_from_row(row)

    def retry_safe_job(self, job_id: str) -> RuntimeJob:
        """Requeue one failed job whose latest attempt is explicitly retry-safe."""
        now = _utc_now()
        with self._connect() as connection, _transaction(connection):
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job_row is None:
                raise JobNotFoundError(job_id)
            job = _job_from_row(job_row)
            if job.status is not JobStatus.FAILED:
                raise InvalidStateTransitionError(
                    f"only failed jobs can be retried; {job_id} is {job.status.value}"
                )
            attempt_row = connection.execute(
                """
                SELECT * FROM job_attempts
                WHERE job_id = ? ORDER BY attempt_number DESC LIMIT 1
                """,
                (job_id,),
            ).fetchone()
            if attempt_row is None:
                raise InvalidStateTransitionError(
                    f"job {job_id} has no completed attempt to retry"
                )
            attempt = _attempt_from_row(attempt_row)
            if attempt.status not in {
                JobAttemptStatus.FAILED,
                JobAttemptStatus.INTERRUPTED,
            } or not _retry_safe(attempt.idempotency_class):
                raise InvalidStateTransitionError("latest attempt is not safe to retry")
            next_max_attempts = max(job.max_attempts, attempt.attempt_number + 1)
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, available_at = ?, terminal_at = NULL,
                    cancel_requested_at = NULL, max_attempts = ?,
                    error_summary = NULL, updated_at = ?, version = version + 1
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.QUEUED.value,
                    now,
                    next_max_attempts,
                    now,
                    job_id,
                    JobStatus.FAILED.value,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ConcurrentUpdateError(f"job {job_id} changed concurrently")
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        result = _job_from_row(updated)
        self.wake_workers()
        return result
