"""Runtime queue claim workload."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
import threading
from typing import Any, Final

from gpt2giga_harness.performance_workloads import WorkloadSpec
from gpt2giga_harness.performance_workloads.runtime.contracts import (
    RuntimeCase,
    RuntimeCaseFactory,
)
from gpt2giga_harness.performance_workloads.runtime.fixtures import seed_jobs
from gpt2giga_harness.performance_workloads.runtime.instrumentation import (
    RuntimeCounters,
    TracingRuntimeStore,
)
from gpt2giga_harness.runtime.jobs.claims import (
    CLAIM_CANDIDATE_WINDOW,
    CLAIM_MAX_CAS_RETRIES,
)


QUEUE_SIZE: Final[int] = 10_000
WORKER_FINGERPRINT: Final[dict[str, Any]] = {
    "os": "fixture",
    "harnesses": {},
}
WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="runtime.queue.claim",
        family="runtime/queue",
        profiles=("runtime-detail",),
        variants=(
            "queued_10000",
            "incompatible_90_percent",
            "compatible_at_window_end",
            "workers_2",
            "workers_8",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "sqlite_connections",
            "sqlite_statements",
            "rows_parsed",
            "claimed_jobs",
            "duplicate_claims",
        ),
        future_gate="G-PERF",
    ),
)


def case_factories() -> tuple[RuntimeCaseFactory, ...]:
    """Return bounded queue cases in deterministic registry order."""
    return (
        _claim_factory("queued_10000", incompatible=0, expected_index=0),
        _claim_factory(
            "incompatible_90_percent",
            incompatible=9_000,
            expected_index=9_000,
        ),
        _claim_factory(
            "compatible_at_window_end",
            incompatible=9_000,
            expected_index=9_999,
            excluded_before_end=999,
        ),
        _parallel_claim_factory(2),
        _parallel_claim_factory(8),
    )


def _claim_factory(
    variant: str,
    *,
    incompatible: int,
    expected_index: int,
    excluded_before_end: int = 0,
) -> RuntimeCaseFactory:
    def factory(root: Path) -> RuntimeCase:
        store = TracingRuntimeStore(root)
        seed_jobs(
            store,
            count=QUEUE_SIZE,
            incompatible=incompatible,
            excluded_before_end=excluded_before_end,
        )

        def operation(counters: RuntimeCounters) -> dict[str, int]:
            rows_before = store.claim_rows_snapshot()
            claim = store.claim_next_job(
                worker_id="fixture-worker",
                capability_fingerprint=WORKER_FINGERPRINT,
                lease_seconds=5,
            )
            expected = f"job-{expected_index:05d}"
            if claim is None or claim.job.id != expected:
                raise RuntimeError(f"queue fixture did not claim {expected}")
            counters.rows_parsed = store.claim_rows_snapshot() - rows_before
            counters.claimed_jobs = 1
            return {
                "candidate_rows_inspected": counters.rows_parsed,
                "candidate_window": CLAIM_CANDIDATE_WINDOW,
                "compatible_candidate_position": (
                    expected_index + 1 - excluded_before_end
                ),
                "compatible_queue_position": expected_index + 1,
                "excluded_jobs": excluded_before_end,
                "incompatible_jobs": incompatible,
            }

        return RuntimeCase(
            id=f"runtime.queue.claim.{variant}",
            family="runtime/queue",
            fixture={
                "queued_jobs": QUEUE_SIZE,
                "incompatible_jobs": incompatible,
                "excluded_jobs": excluded_before_end,
                "workers": 1,
            },
            store=store,
            operation=operation,
        )

    return factory


def _parallel_claim_factory(worker_count: int) -> RuntimeCaseFactory:
    def factory(root: Path) -> RuntimeCase:
        store = TracingRuntimeStore(root)
        seed_jobs(store, count=QUEUE_SIZE)

        def operation(counters: RuntimeCounters) -> dict[str, int]:
            rows_before = store.claim_rows_snapshot()
            barrier = threading.Barrier(worker_count)

            def claim(worker_index: int) -> str:
                barrier.wait(timeout=5)
                item = store.claim_next_job(
                    worker_id=f"fixture-worker-{worker_index}",
                    capability_fingerprint=WORKER_FINGERPRINT,
                    lease_seconds=5,
                )
                if item is None:
                    raise RuntimeError("parallel queue fixture missed a claim")
                return item.job.id

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=worker_count
            ) as executor:
                claimed = list(executor.map(claim, range(worker_count)))
            counters.claimed_jobs = len(claimed)
            counters.duplicate_claims = len(claimed) - len(set(claimed))
            counters.rows_parsed = store.claim_rows_snapshot() - rows_before
            if counters.duplicate_claims:
                raise RuntimeError("parallel queue fixture duplicated a claim")
            return {
                "candidate_row_budget": (
                    worker_count * CLAIM_MAX_CAS_RETRIES * CLAIM_CANDIDATE_WINDOW
                ),
                "candidate_rows_inspected": counters.rows_parsed,
                "workers": worker_count,
                "claimed_jobs": len(claimed),
                "duplicate_claims": counters.duplicate_claims,
            }

        return RuntimeCase(
            id=f"runtime.queue.claim.workers_{worker_count}",
            family="runtime/queue",
            fixture={"queued_jobs": QUEUE_SIZE, "workers": worker_count},
            store=store,
            operation=operation,
        )

    return factory
