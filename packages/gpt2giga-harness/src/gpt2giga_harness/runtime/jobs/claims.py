"""Claim compatible queued jobs behind the runtime facade."""

from __future__ import annotations

from typing import Any, Mapping

from gpt2giga_harness.runtime.db.transactions import transaction as _transaction
from gpt2giga_harness.runtime.models import (
    ClaimedJob,
    JobAttemptStatus,
    JobStatus,
)
from gpt2giga_harness.runtime.repositories.base import RuntimeRepository
from gpt2giga_harness.runtime.repositories.records import (
    _attempt_from_row,
    _fingerprint_matches,
    _future_time,
    _job_from_row,
    _json_mapping,
    _new_id,
    _optional_text,
    _required_text,
    _safe_json,
    _utc_now,
)


class JobClaimsRepository(RuntimeRepository):
    """Claim compatible queued jobs behind the runtime facade."""

    def claim_next_job(
        self,
        *,
        worker_id: str,
        capability_fingerprint: Mapping[str, Any],
        lease_seconds: float,
    ) -> ClaimedJob | None:
        """Atomically claim the first due job supported by this worker."""
        worker_id = _required_text(worker_id, "worker_id")
        now = _utc_now()
        leased_until = _future_time(max(lease_seconds, 1.0))
        with self._connect() as connection, _transaction(connection):
            rows = connection.execute(
                """
                SELECT candidate.* FROM jobs AS candidate
                WHERE candidate.status = ?
                  AND (candidate.available_at IS NULL OR candidate.available_at <= ?)
                  AND candidate.cancel_requested_at IS NULL
                  AND (
                    candidate.origin != 'interactive'
                    OR NOT EXISTS (
                      SELECT 1 FROM jobs AS blocker
                      WHERE blocker.session_id = candidate.session_id
                        AND blocker.id != candidate.id
                        AND (
                          blocker.status = ?
                          OR (
                            blocker.status IN (?, ?, ?, ?)
                            AND (
                              blocker.created_at < candidate.created_at
                              OR (
                                blocker.created_at = candidate.created_at
                                AND blocker.id < candidate.id
                              )
                            )
                          )
                        )
                    )
                  )
                ORDER BY candidate.priority DESC, candidate.created_at, candidate.id
                """,
                (
                    JobStatus.QUEUED.value,
                    now,
                    JobStatus.RUNNING.value,
                    JobStatus.QUEUED.value,
                    JobStatus.RETRY_WAIT.value,
                    JobStatus.WAITING_APPROVAL.value,
                    JobStatus.WAITING_INPUT.value,
                ),
            ).fetchall()
            job_row = next(
                (
                    row
                    for row in rows
                    if _fingerprint_matches(
                        _json_mapping(row["required_fingerprint_json"]),
                        capability_fingerprint,
                        required_harness_id=_optional_text(row["required_harness_id"]),
                    )
                ),
                None,
            )
            if job_row is None:
                return None
            job = _job_from_row(job_row)
            count_row = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM job_attempts WHERE job_id = ?",
                (job.id,),
            ).fetchone()
            attempt_number = int(count_row[0]) + 1
            if attempt_number > job.max_attempts:
                connection.execute(
                    "UPDATE jobs SET status = ?, terminal_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                    (JobStatus.FAILED.value, now, now, job.id),
                )
                return None
            run_id = job.initial_run_id if attempt_number == 1 else _new_id("run")
            attempt_id = _new_id("attempt")
            connection.execute(
                """
                INSERT INTO job_attempts (
                    id, job_id, attempt_number, status, run_id, lease_owner,
                    leased_until, heartbeat_at, idempotency_class,
                    capability_fingerprint_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    job.id,
                    attempt_number,
                    JobAttemptStatus.CLAIMED.value,
                    run_id,
                    worker_id,
                    leased_until,
                    now,
                    "unknown",
                    _safe_json(capability_fingerprint),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND status = ?
                """,
                (JobStatus.RUNNING.value, now, job.id, JobStatus.QUEUED.value),
            )
            claimed_job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job.id,)
            ).fetchone()
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return ClaimedJob(
            job=_job_from_row(claimed_job), attempt=_attempt_from_row(attempt_row)
        )
