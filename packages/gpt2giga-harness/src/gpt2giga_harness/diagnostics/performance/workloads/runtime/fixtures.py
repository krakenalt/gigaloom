"""Canonical content-free fixtures and sanitized SQLite query plans."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Final

from gpt2giga_harness.diagnostics.performance.workloads.runtime.instrumentation import (
    TracingRuntimeStore,
)
from gpt2giga_harness.runtime.jobs.claims import _candidate_query


FIXED_AT: Final[str] = "2026-01-01T00:00:00+00:00"
REVISION_JOBS_QUERY: Final[str] = "SELECT COUNT(*), COALESCE(SUM(version), 0) FROM jobs"


def seed_jobs(
    store: TracingRuntimeStore,
    *,
    count: int,
    incompatible: int = 0,
    excluded_before_end: int = 0,
) -> None:
    """Insert ordered canonical queue rows outside the measured window."""
    rows = (
        (
            f"job-{index:05d}",
            f"key-{index:05d}",
            f"session-{index:05d}",
            f"message-{index:05d}",
            f"run-{index:05d}",
            "incompatible" if index < incompatible else None,
            json.dumps({"os": "incompatible"}) if index < incompatible else "{}",
            (
                FIXED_AT
                if count - excluded_before_end - 1 <= index < count - 1
                else None
            ),
            f"2026-01-01T00:00:{index // 1000:02d}.{index % 1000:03d}+00:00",
        )
        for index in range(count)
    )
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            """
            INSERT INTO jobs (
                id, origin, idempotency_key_hash, status, session_id,
                user_message_id, initial_run_id, available_at, max_attempts,
                priority, required_os, required_fingerprint_json, cancel_requested_at,
                created_at, updated_at
            ) VALUES (?, 'manual', ?, 'queued', ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)
            """,
            (
                (
                    job_id,
                    key,
                    session,
                    message,
                    run_id,
                    FIXED_AT,
                    required_os,
                    required,
                    canceled_at,
                    at,
                    at,
                )
                for (
                    job_id,
                    key,
                    session,
                    message,
                    run_id,
                    required_os,
                    required,
                    canceled_at,
                    at,
                ) in rows
            ),
        )


def seed_revision_rows(store: TracingRuntimeStore, *, count: int) -> None:
    """Insert the canonical row population consumed by runs-center revision."""
    seed_jobs(store, count=count)


def explain_query_plan(
    path: Path,
    *,
    query_id: str,
    sql: str,
    parameters: Sequence[Any] = (),
) -> dict[str, Any]:
    """Return a query plan without SQL text, parameter values, or private paths."""
    normalized = " ".join(sql.split())
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            f"EXPLAIN QUERY PLAN {sql}", tuple(parameters)
        ).fetchall()
    return {
        "id": query_id,
        "sql_sha256": hashlib.sha256(normalized.encode()).hexdigest(),
        "parameter_count": len(parameters),
        "steps": [
            {
                "select_id": int(row[0]),
                "parent_id": int(row[1]),
                "detail": str(row[3]),
            }
            for row in rows
        ],
    }


def build_query_plans(root: Path) -> list[dict[str, Any]]:
    """Capture stable plans for runtime scaling and maintenance reads."""
    store = TracingRuntimeStore(root)
    claim_query, claim_parameters = _candidate_query(
        now=FIXED_AT,
        worker_os="fixture",
        available_harness_ids=(),
        cursor=None,
    )
    specs: Iterable[tuple[str, str, Sequence[Any]]] = (
        (
            "queue_claim",
            claim_query,
            claim_parameters,
        ),
        ("runs_center_jobs", REVISION_JOBS_QUERY, ()),
        (
            "runs_center_approvals",
            "SELECT status, COUNT(*), COALESCE(MAX(COALESCE(decided_at, created_at)), '') "
            "FROM approval_requests GROUP BY status ORDER BY status",
            (),
        ),
        (
            "runs_center_workers",
            "SELECT status, COUNT(*), COALESCE(MAX(COALESCE(stopped_at, started_at)), '') "
            "FROM workers GROUP BY status ORDER BY status",
            (),
        ),
        (
            "expired_attempt_recovery",
            "SELECT * FROM job_attempts WHERE status IN (?, ?, ?) "
            "AND leased_until IS NOT NULL AND leased_until < ? ORDER BY leased_until, id",
            ("claimed", "starting", "running", FIXED_AT),
        ),
        (
            "schedule_due",
            "SELECT * FROM schedule_states WHERE enabled = 1 AND status = 'active' "
            "AND next_run_at <= ? ORDER BY next_run_at",
            (FIXED_AT,),
        ),
        (
            "reconcile_attempts",
            "SELECT * FROM job_attempts ORDER BY created_at, attempt_number, id",
            (),
        ),
        (
            "reconcile_outbox",
            "SELECT * FROM runtime_outbox WHERE processed_at IS NULL "
            "ORDER BY created_at, id LIMIT ?",
            (1_000,),
        ),
    )
    return [
        explain_query_plan(store.path, query_id=query_id, sql=sql, parameters=params)
        for query_id, sql, params in specs
    ]
