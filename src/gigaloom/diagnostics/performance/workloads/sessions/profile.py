"""Session persistence scaling profile built from modular workload cases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import statistics
import tempfile
import time
from typing import Any, Final

from gigaloom.diagnostics.performance.workloads.instrumentation import (
    observe_session_storage,
)
from gigaloom.diagnostics.performance.workloads.sessions import (
    catalog,
    events,
    messages,
    runs,
)
from gigaloom.diagnostics.performance.workloads.sessions.cases import (
    CaseFactory,
    StorageCase,
)


SCHEMA_VERSION: Final[str] = "gigaloom.session-storage-baseline.v1"
FIXTURE_SET_VERSION: Final[str] = "session-workload.v1"


@dataclass(frozen=True, slots=True)
class _Observation:
    wall_ms: float
    cpu_ms: float
    counters: dict[str, int]
    details: dict[str, int | float]


def run_session_storage_scaling_baseline(*, samples: int) -> dict[str, Any]:
    """Measure current session persistence algorithms on content-free fixtures."""
    if not 1 <= samples <= 100:
        raise ValueError("samples must be between 1 and 100")
    factories: tuple[CaseFactory, ...] = (
        *catalog.case_factories(),
        *messages.case_factories(),
        *runs.case_factories(),
        *events.case_factories(),
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="gigaloom-session-perf-") as raw_root:
        root = Path(raw_root)
        for index, factory in enumerate(factories):
            case_root = root / f"case-{index:02d}"
            case = factory(case_root)
            results.append(_measure_case(case, samples=samples))
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_version": FIXTURE_SET_VERSION,
        "samples_per_case": samples,
        "privacy": {
            "content_captured": False,
            "secrets_captured": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "network_accessed": False,
            "temporary_state_only": True,
        },
        "measurement_contract": {
            "fixture_setup_in_measured_window": False,
            "fixture_setup": "direct_content_free_canonical_state",
            "production_store_used_in_measured_window": True,
            "durability_disabled_in_measured_window": False,
            "absolute_wall_time_is_ci_blocking": False,
            "algorithmic_counters_are_ci_stable": True,
            "catalog_scales": list(catalog.CATALOG_SCALES),
            "message_history": 5_000,
            "message_tail": 20,
            "run_scales": list(runs.RUN_SCALES),
            "event_history": events.EVENT_HISTORY_SIZE,
            "steady_events": 100,
            "burst_events": 500,
        },
        "results": results,
    }


def _measure_case(case: StorageCase, *, samples: int) -> dict[str, Any]:
    observations: list[_Observation] = []
    for _index in range(samples):
        with observe_session_storage() as counters:
            before_wall = time.perf_counter_ns()
            before_cpu = time.process_time_ns()
            details = case.operation(counters)
            after_cpu = time.process_time_ns()
            after_wall = time.perf_counter_ns()
        observations.append(
            _Observation(
                wall_ms=(after_wall - before_wall) / 1_000_000,
                cpu_ms=(after_cpu - before_cpu) / 1_000_000,
                counters=counters.snapshot(),
                details=dict(details),
            )
        )
        if case.reset is not None and _index + 1 < samples:
            case.reset()
    wall = sorted(item.wall_ms for item in observations)
    cpu = sorted(item.cpu_ms for item in observations)
    result: dict[str, Any] = {
        "id": case.id,
        "family": case.family,
        "fixture": dict(case.fixture),
        "samples": samples,
        "measured_window": "operation_only",
        "regression_gate": {
            "blocking": False,
            "classification": "reference_wall_time_algorithmic_counters",
        },
        "wall_ms": _summary(wall),
        "cpu_ms": _summary(cpu),
        "counters": {
            name: _summary(sorted(float(item.counters[name]) for item in observations))
            for name in sorted(observations[0].counters)
        },
        "details": {
            name: _summary(sorted(float(item.details[name]) for item in observations))
            for name in sorted(observations[0].details)
        },
    }
    if "appended_events" in observations[0].details:
        throughput = sorted(
            float(item.details["appended_events"]) / max(item.wall_ms / 1_000, 1e-9)
            for item in observations
        )
        result["throughput_per_second"] = _summary(throughput)
    return result


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
        "mean": round(statistics.fmean(values), 3),
        "max": round(max(values), 3),
    }


def _percentile(values: list[float], percentile: int) -> float:
    if len(values) == 1:
        return round(values[0], 3)
    rank = (percentile / 100) * (len(values) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return round(values[lower] + (values[upper] - values[lower]) * weight, 3)
