"""Runtime queue, revision, and worker scaling baseline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import statistics
import tempfile
import time
from typing import Any, Final

from gigaloom.diagnostics.performance.workloads.runtime import (
    queue,
    revisions,
    worker,
)
from gigaloom.diagnostics.performance.workloads.runtime.contracts import (
    RuntimeCase,
)
from gigaloom.diagnostics.performance.workloads.runtime.evidence import (
    environment,
    resource_snapshot,
    source_commit,
)
from gigaloom.diagnostics.performance.workloads.runtime.fixtures import (
    build_query_plans,
)
from gigaloom.diagnostics.performance.workloads.runtime.instrumentation import (
    RuntimeCounters,
    apply_sql_delta,
)


SCHEMA_VERSION: Final[str] = "gigaloom.runtime-scaling-baseline.v1"
FIXTURE_SET_VERSION: Final[str] = "t01-3.v1"


@dataclass(frozen=True, slots=True)
class _Observation:
    wall_ms: float
    cpu_ms: float
    rss_bytes: int
    counters: dict[str, int]
    details: dict[str, int | float]


def run_runtime_scaling_baseline(*, samples: int) -> dict[str, Any]:
    """Measure current runtime algorithms on prepared temporary fixtures."""
    if not 1 <= samples <= 100:
        raise ValueError("samples must be between 1 and 100")
    factories = (
        *queue.case_factories(),
        *revisions.case_factories(),
        *worker.case_factories(),
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="gigaloom-runtime-scaling-") as raw_root:
        root = Path(raw_root)
        for case_index, factory in enumerate(factories):
            observations = []
            fixture: dict[str, int | str | bool] = {}
            for sample_index in range(samples):
                case = factory(root / f"case-{case_index:02d}-{sample_index:03d}")
                fixture = dict(case.fixture)
                observations.append(_measure_case(case))
            results.append(_summarize_case(case, fixture, observations))
        query_plans = build_query_plans(root / "query-plans")
    environment_fields = environment()
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_version": FIXTURE_SET_VERSION,
        "profile": "runtime-detail",
        "source_commit": source_commit(),
        "samples_per_case": samples,
        "environment": environment_fields,
        "privacy": {
            "content_captured": False,
            "secrets_captured": False,
            "private_paths_captured": False,
            "sql_parameter_values_captured": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "network_accessed": False,
            "temporary_state_only": True,
        },
        "measurement_contract": {
            "fixture_setup_in_measured_window": False,
            "fixture_setup": "direct_content_free_canonical_state",
            "production_store_used_in_measured_window": True,
            "queue_size": queue.QUEUE_SIZE,
            "incompatible_percent": 90,
            "worker_counts": [2, 8],
            "revision_scales": list(revisions.REVISION_SCALES),
            "maintenance_cases": [
                "heartbeat",
                "idle",
                "idle_minute",
                "schedule",
                "recovery",
                "reconcile",
            ],
            "absolute_wall_time_is_ci_blocking": False,
            "algorithmic_counters_are_ci_stable": True,
            "query_plan_sql_retained": False,
            "query_plan_parameter_values_retained": False,
        },
        "query_plans": query_plans,
        "results": results,
    }


def _measure_case(case: RuntimeCase) -> _Observation:
    counters = RuntimeCounters()
    sql_before = case.store.trace_snapshot()
    rss_before, wakeups_before = resource_snapshot()
    wall_before = time.perf_counter_ns()
    cpu_before = time.process_time_ns()
    details = case.operation(counters)
    cpu_after = time.process_time_ns()
    wall_after = time.perf_counter_ns()
    rss_after, wakeups_after = resource_snapshot()
    apply_sql_delta(counters, sql_before, case.store.trace_snapshot())
    counters.wakeups = max(wakeups_after - wakeups_before, 0)
    return _Observation(
        wall_ms=(wall_after - wall_before) / 1_000_000,
        cpu_ms=(cpu_after - cpu_before) / 1_000_000,
        rss_bytes=max(rss_after, rss_before),
        counters=counters.snapshot(),
        details=dict(details),
    )


def _summarize_case(
    case: RuntimeCase,
    fixture: dict[str, int | str | bool],
    observations: list[_Observation],
) -> dict[str, Any]:
    return {
        "id": case.id,
        "family": case.family,
        "fixture": fixture,
        "samples": len(observations),
        "measured_window": "operation_only",
        "regression_gate": {
            "blocking": False,
            "classification": "reference_wall_time_algorithmic_counters",
        },
        "wall_ms": _summary([item.wall_ms for item in observations]),
        "cpu_ms": _summary([item.cpu_ms for item in observations]),
        "rss_bytes": _summary([float(item.rss_bytes) for item in observations]),
        "counters": {
            key: _summary([float(item.counters[key]) for item in observations])
            for key in sorted(observations[0].counters)
        },
        "details": {
            key: _summary([float(item.details[key]) for item in observations])
            for key in sorted(observations[0].details)
        },
    }


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "p99": _percentile(ordered, 99),
        "mean": round(statistics.fmean(ordered), 3),
        "max": round(max(ordered), 3),
    }


def _percentile(values: list[float], percentile: int) -> float:
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)
