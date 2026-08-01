"""Bounded, content-free durable runtime performance profiling."""

from __future__ import annotations

import statistics
import sys
import time
from typing import Any, Final

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None

from gigaloom.runtime.worker import (
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_POLL_SECONDS,
)

from .runtime import (
    _OperationSample,
    _ResourceSnapshot,
    _SqlCounts,
    _TracingRuntimeStore,
)


SCHEMA_VERSION: Final[str] = "gigaloom.runtime-performance-profile.v3"
FIXTURE_SET_VERSION: Final[str] = "runtime-performance.v1"
MAX_SAMPLES: Final[int] = 100
QUEUE_SCALE: Final[int] = 16
RUN_UPDATE_SCALE: Final[int] = 16
IDLE_WINDOW_SECONDS: Final[float] = 0.56
IDLE_POLL_SECONDS: Final[float] = DEFAULT_POLL_SECONDS
IDLE_MAX_SECONDS: Final[float] = DEFAULT_MAX_IDLE_SECONDS
LOCK_HOLD_SECONDS: Final[float] = 0.015
MAX_IDLE_CYCLES_PER_MINUTE: Final[float] = 65.0
MAX_WAKE_LATENCY_MS: Final[float] = 250.0

REQUIRED_COVERAGE: Final[dict[str, tuple[str, ...]]] = {
    "resources": (
        "worker_idle_cycle",
        "worker_idle_loop",
        "worker_wakeup_signal",
        "worker_active_echo",
    ),
    "sqlite_and_queue": (
        "queue_claim_one",
        "queue_claim_many",
        "sqlite_lock_contention",
    ),
    "worker_lifecycle": (
        "worker_startup",
        "schedule_scan_empty",
        "worker_heartbeat",
        "retry_requeue",
        "cancel_request",
        "expired_lease_recovery",
        "runtime_reconcile",
        "worker_shutdown",
    ),
    "delivery_and_surfaces": (
        "web_app_startup",
        "api_defaults",
        "api_session_events",
        "sse_terminal_attach",
        "web_payload_projection",
    ),
    "filesystem": ("session_run_update",),
}


def _measure(
    metric_id: str,
    operation: Any,
    store: _TracingRuntimeStore | None = None,
) -> _OperationSample:
    sql_before = store.trace_snapshot() if store is not None else _SqlCounts()
    resource_before = _resource_snapshot()
    wall_before = time.perf_counter_ns()
    cpu_before = time.process_time_ns()
    details = operation()
    cpu_after = time.process_time_ns()
    wall_after = time.perf_counter_ns()
    resource_after = _resource_snapshot()
    sql_after = store.trace_snapshot() if store is not None else _SqlCounts()
    return _OperationSample(
        id=metric_id,
        wall_ms=(wall_after - wall_before) / 1_000_000,
        cpu_ms=(cpu_after - cpu_before) / 1_000_000,
        peak_rss_bytes=resource_after.peak_rss_bytes,
        wakeups=max(
            (
                resource_after.voluntary_switches
                + resource_after.involuntary_switches
                - resource_before.voluntary_switches
                - resource_before.involuntary_switches
            ),
            0,
        ),
        sqlite=sql_after - sql_before,
        sqlite_observed=store is not None,
        details={key: float(value) for key, value in dict(details).items()},
    )


def _summarize(
    metric_id: str,
    samples: list[_OperationSample],
) -> dict[str, Any]:
    detail_keys = sorted({key for sample in samples for key in sample.details})
    summary = {
        "id": metric_id,
        "wall_ms": _percentiles([sample.wall_ms for sample in samples]),
        "cpu_ms": _percentiles([sample.cpu_ms for sample in samples]),
        "peak_rss_bytes": _percentiles(
            [float(sample.peak_rss_bytes) for sample in samples]
        ),
        "wakeups": _percentiles([float(sample.wakeups) for sample in samples]),
        "sqlite": {
            "observed": all(sample.sqlite_observed for sample in samples),
            "reads": (
                _percentiles([float(sample.sqlite.reads) for sample in samples])
                if all(sample.sqlite_observed for sample in samples)
                else None
            ),
            "writes": (
                _percentiles([float(sample.sqlite.writes) for sample in samples])
                if all(sample.sqlite_observed for sample in samples)
                else None
            ),
            "schema": (
                _percentiles([float(sample.sqlite.schema) for sample in samples])
                if all(sample.sqlite_observed for sample in samples)
                else None
            ),
            "connections": (
                _percentiles([float(sample.sqlite.connections) for sample in samples])
                if all(sample.sqlite_observed for sample in samples)
                else None
            ),
        },
        "details": {
            key: _percentiles([sample.details.get(key, 0.0) for sample in samples])
            for key in detail_keys
        },
        "optimization_target": None,
        "target_status": "reference_only_not_selected",
    }
    if metric_id == "worker_idle_loop":
        summary["optimization_target"] = {
            "projected_steady_cycles_per_minute_max": (MAX_IDLE_CYCLES_PER_MINUTE)
        }
        summary["target_status"] = (
            "within_target"
            if summary["details"]["projected_steady_cycles_per_minute"]["p95"]
            <= MAX_IDLE_CYCLES_PER_MINUTE
            else "failed"
        )
    elif metric_id == "worker_wakeup_signal":
        summary["optimization_target"] = {
            "wall_p95_ms_max": MAX_WAKE_LATENCY_MS,
            "delivered_p95_min": 1.0,
        }
        summary["target_status"] = (
            "within_target"
            if summary["wall_ms"]["p95"] <= MAX_WAKE_LATENCY_MS
            and summary["details"]["delivered"]["p95"] >= 1.0
            else "failed"
        )
    return summary


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "p99": _percentile(ordered, 99),
        "mean": round(statistics.fmean(ordered), 3),
    }


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)


def _resource_snapshot() -> _ResourceSnapshot:
    if resource is None:
        return _ResourceSnapshot(0, 0, 0)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    rss = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    return _ResourceSnapshot(
        peak_rss_bytes=rss,
        voluntary_switches=int(usage.ru_nvcsw),
        involuntary_switches=int(usage.ru_nivcsw),
    )
