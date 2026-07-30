"""Claim compatible queued jobs behind the runtime facade."""

from __future__ import annotations

from collections.abc import Sequence
import sqlite3
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

CLAIM_CANDIDATE_WINDOW = 64
CLAIM_MAX_CAS_RETRIES = 8


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
        available_harness_ids = _available_harness_ids(capability_fingerprint)
        worker_os = _optional_text(capability_fingerprint.get("os"))
        for _ in range(CLAIM_MAX_CAS_RETRIES):
            job_row = self._find_compatible_candidate(
                capability_fingerprint=capability_fingerprint,
                available_harness_ids=available_harness_ids,
                worker_os=worker_os,
            )
            if job_row is None:
                return None
            claimed = self._claim_candidate(
                job_row,
                worker_id=worker_id,
                capability_fingerprint=capability_fingerprint,
                lease_seconds=lease_seconds,
            )
            if claimed is not None:
                return claimed
        return None

    def _find_compatible_candidate(
        self,
        *,
        capability_fingerprint: Mapping[str, Any],
        available_harness_ids: Sequence[str],
        worker_os: str | None,
    ) -> sqlite3.Row | None:
        cursor: tuple[int, str, str] | None = None
        with self._connect() as connection:
            while True:
                sql, parameters = _candidate_query(
                    now=_utc_now(),
                    worker_os=worker_os,
                    available_harness_ids=available_harness_ids,
                    cursor=cursor,
                )
                rows = connection.execute(sql, parameters).fetchall()
                self._observe_claim_candidates(len(rows))
                for row in rows:
                    if _fingerprint_matches(
                        _json_mapping(row["required_fingerprint_json"]),
                        capability_fingerprint,
                        required_harness_id=_optional_text(row["required_harness_id"]),
                    ):
                        return row
                if len(rows) < CLAIM_CANDIDATE_WINDOW:
                    return None
                last = rows[-1]
                cursor = (
                    int(last["priority"]),
                    str(last["created_at"]),
                    str(last["id"]),
                )

    def _claim_candidate(
        self,
        candidate: sqlite3.Row,
        *,
        worker_id: str,
        capability_fingerprint: Mapping[str, Any],
        lease_seconds: float,
    ) -> ClaimedJob | None:
        now = _utc_now()
        attempt_id = _new_id("attempt")
        retry_run_id = _new_id("run")
        leased_until = _future_time(max(lease_seconds, 1.0))
        fingerprint_json = _safe_json(capability_fingerprint)
        with self._connect() as connection, _transaction(connection):
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, version = version + 1
                WHERE id = ? AND status = ? AND version = ?
                  AND cancel_requested_at IS NULL
                  AND (available_at IS NULL OR available_at <= ?)
                """,
                (
                    JobStatus.RUNNING.value,
                    now,
                    candidate["id"],
                    JobStatus.QUEUED.value,
                    candidate["version"],
                    now,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                return None
            count_row = connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) FROM job_attempts WHERE job_id = ?",
                (candidate["id"],),
            ).fetchone()
            attempt_number = int(count_row[0]) + 1
            if attempt_number > int(candidate["max_attempts"]):
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, terminal_at = ?, updated_at = ?
                    WHERE id = ? AND status = ? AND version = ?
                    """,
                    (
                        JobStatus.FAILED.value,
                        now,
                        now,
                        candidate["id"],
                        JobStatus.RUNNING.value,
                        int(candidate["version"]) + 1,
                    ),
                )
                return None
            run_id = (
                str(candidate["initial_run_id"])
                if attempt_number == 1
                else retry_run_id
            )
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
                    candidate["id"],
                    attempt_number,
                    JobAttemptStatus.CLAIMED.value,
                    run_id,
                    worker_id,
                    leased_until,
                    now,
                    "unknown",
                    fingerprint_json,
                    now,
                    now,
                ),
            )
        with self._connect() as connection:
            claimed_job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (candidate["id"],)
            ).fetchone()
            attempt_row = connection.execute(
                "SELECT * FROM job_attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
        return ClaimedJob(
            job=_job_from_row(claimed_job), attempt=_attempt_from_row(attempt_row)
        )

    def _observe_claim_candidates(self, count: int) -> None:
        """Allow benchmark stores to count bounded candidate rows."""


def _available_harness_ids(fingerprint: Mapping[str, Any]) -> tuple[str, ...]:
    harnesses = fingerprint.get("harnesses")
    if not isinstance(harnesses, Mapping):
        return ()
    return tuple(
        sorted(
            str(harness_id)
            for harness_id, details in harnesses.items()
            if isinstance(details, Mapping) and bool(details.get("available"))
        )
    )


def _candidate_query(
    *,
    now: str,
    worker_os: str | None,
    available_harness_ids: Sequence[str],
    cursor: tuple[int, str, str] | None,
) -> tuple[str, tuple[Any, ...]]:
    clauses = [
        "candidate.status = ?",
        "(candidate.available_at IS NULL OR candidate.available_at <= ?)",
        "candidate.cancel_requested_at IS NULL",
    ]
    parameters: list[Any] = [JobStatus.QUEUED.value, now]
    if worker_os is None:
        clauses.append("candidate.required_os IS NULL")
    else:
        clauses.append("(candidate.required_os IS NULL OR candidate.required_os = ?)")
        parameters.append(worker_os)
    if available_harness_ids:
        placeholders = ", ".join("?" for _ in available_harness_ids)
        clauses.append(
            "(candidate.required_harness_id IS NULL "
            f"OR candidate.required_harness_id IN ({placeholders}))"
        )
        parameters.extend(available_harness_ids)
    else:
        clauses.append("candidate.required_harness_id IS NULL")
    clauses.append(
        """
        (
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
        """
    )
    parameters.extend(
        (
            JobStatus.RUNNING.value,
            JobStatus.QUEUED.value,
            JobStatus.RETRY_WAIT.value,
            JobStatus.WAITING_APPROVAL.value,
            JobStatus.WAITING_INPUT.value,
        )
    )
    if cursor is not None:
        priority, created_at, job_id = cursor
        clauses.append(
            """
            (
              candidate.priority < ?
              OR (
                candidate.priority = ?
                AND (
                  candidate.created_at > ?
                  OR (
                    candidate.created_at = ?
                    AND candidate.id > ?
                  )
                )
              )
            )
            """
        )
        parameters.extend((priority, priority, created_at, created_at, job_id))
    parameters.append(CLAIM_CANDIDATE_WINDOW)
    sql = (
        "SELECT candidate.* FROM jobs AS candidate "
        "INDEXED BY jobs_queue_claim_idx WHERE "
        + " AND ".join(clauses)
        + " ORDER BY candidate.priority DESC, candidate.created_at, candidate.id"
        + " LIMIT ?"
    )
    return sql, tuple(parameters)
