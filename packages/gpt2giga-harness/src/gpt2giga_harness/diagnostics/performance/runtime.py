"""Bounded, content-free G6 durable runtime performance profiling."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import platform
import sqlite3
import tempfile
import threading
from typing import Any, Final, Iterator, Mapping

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - exercised on Windows
    resource = None

from gpt2giga_harness.runtime.api import RuntimeCoordinationStore
from gpt2giga_harness.runtime.worker import (
    DEFAULT_MAX_IDLE_SECONDS,
    DEFAULT_POLL_SECONDS,
    DurableJobWorker,
)


SCHEMA_VERSION: Final[str] = "gigaloom.runtime-performance-profile.v3"
FIXTURE_SET_VERSION: Final[str] = "g6-02.v1"
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
        "tui_navigation_load",
    ),
    "filesystem": ("session_run_update",),
}


@dataclass(frozen=True)
class _SqlCounts:
    reads: int = 0
    writes: int = 0
    schema: int = 0
    connections: int = 0

    def __sub__(self, other: _SqlCounts) -> _SqlCounts:
        return _SqlCounts(
            reads=max(self.reads - other.reads, 0),
            writes=max(self.writes - other.writes, 0),
            schema=max(self.schema - other.schema, 0),
            connections=max(self.connections - other.connections, 0),
        )


@dataclass(frozen=True)
class _ResourceSnapshot:
    peak_rss_bytes: int
    voluntary_switches: int
    involuntary_switches: int


@dataclass(frozen=True)
class _OperationSample:
    id: str
    wall_ms: float
    cpu_ms: float
    peak_rss_bytes: int
    wakeups: int
    sqlite: _SqlCounts
    sqlite_observed: bool
    details: Mapping[str, float]


class _TracingRuntimeStore(RuntimeCoordinationStore):
    """Runtime store that counts statements without retaining statement text."""

    def __init__(self, data_dir: str | Path) -> None:
        self._trace_lock = threading.Lock()
        self._trace_counts = _SqlCounts()
        super().__init__(data_dir)

    def trace_snapshot(self) -> _SqlCounts:
        with self._trace_lock:
            return self._trace_counts

    def _record_statement(self, statement: str) -> None:
        operation = statement.lstrip().split(None, 1)[0].upper() if statement else ""
        with self._trace_lock:
            current = self._trace_counts
            if operation in {"SELECT", "WITH"}:
                self._trace_counts = _SqlCounts(
                    reads=current.reads + 1,
                    writes=current.writes,
                    schema=current.schema,
                    connections=current.connections,
                )
            elif operation in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
                self._trace_counts = _SqlCounts(
                    reads=current.reads,
                    writes=current.writes + 1,
                    schema=current.schema,
                    connections=current.connections,
                )
            elif operation in {"CREATE", "ALTER", "DROP"}:
                self._trace_counts = _SqlCounts(
                    reads=current.reads,
                    writes=current.writes,
                    schema=current.schema + 1,
                    connections=current.connections,
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with super()._connect() as connection:
            with self._trace_lock:
                current = self._trace_counts
                self._trace_counts = _SqlCounts(
                    reads=current.reads,
                    writes=current.writes,
                    schema=current.schema,
                    connections=current.connections + 1,
                )
            connection.set_trace_callback(self._record_statement)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)


class _CountingWorker(DurableJobWorker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cycles = 0

    def run_once(self) -> bool:
        self.cycles += 1
        return super().run_once()


def run_runtime_performance_profile(*, samples: int) -> dict[str, Any]:
    """Measure current durable runtime and request paths in temporary state."""
    if not 1 <= samples <= MAX_SAMPLES:
        raise ValueError(f"samples must be between 1 and {MAX_SAMPLES}")
    from gpt2giga_harness.diagnostics.performance.workloads.runtime.profile import (
        run_runtime_scaling_baseline,
    )

    observations: dict[str, list[_OperationSample]] = {}
    with tempfile.TemporaryDirectory(prefix="gigaloom-g6-profile-") as raw_root:
        root = Path(raw_root)
        for index in range(samples):
            for observation in _profile_once(root / f"sample-{index:03d}"):
                observations.setdefault(observation.id, []).append(observation)

    results = [
        _summarize(metric, values) for metric, values in sorted(observations.items())
    ]
    accepted_results = [
        result
        for result in results
        if result["id"] in {"worker_idle_loop", "worker_wakeup_signal"}
    ]
    ranked = sorted(
        (
            {
                "rank": 0,
                "metric": result["id"],
                "wall_p95_ms": result["wall_ms"]["p95"],
                "cpu_p95_ms": result["cpu_ms"]["p95"],
                "sqlite_reads_p95": (
                    result["sqlite"]["reads"]["p95"]
                    if result["sqlite"]["observed"]
                    else None
                ),
                "sqlite_writes_p95": (
                    result["sqlite"]["writes"]["p95"]
                    if result["sqlite"]["observed"]
                    else None
                ),
                "wakeups_p95": result["wakeups"]["p95"],
                "ranking_basis": (
                    "cpu_p95_ms"
                    if result["id"] == "worker_idle_loop"
                    else "wall_p95_ms"
                ),
                "ranking_score_ms": (
                    result["cpu_ms"]["p95"]
                    if result["id"] == "worker_idle_loop"
                    else result["wall_ms"]["p95"]
                ),
            }
            for result in results
        ),
        key=lambda item: (-item["ranking_score_ms"], item["metric"]),
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank

    observed_ids = set(observations)
    missing = {
        family: sorted(set(metrics) - observed_ids)
        for family, metrics in REQUIRED_COVERAGE.items()
        if set(metrics) - observed_ids
    }
    runtime_scaling_baseline = run_runtime_scaling_baseline(samples=samples)
    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_version": FIXTURE_SET_VERSION,
        "source_commit": runtime_scaling_baseline["source_commit"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "profile": "runtime-detail",
        "samples_per_probe": samples,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
        },
        "privacy": {
            "content_captured": False,
            "secrets_captured": False,
            "native_homes_accessed": False,
            "provider_traffic": False,
            "network_accessed": False,
            "temporary_state_only": True,
        },
        "measurement_contract": {
            "required_coverage": {
                family: list(metrics) for family, metrics in REQUIRED_COVERAGE.items()
            },
            "queue_scale": QUEUE_SCALE,
            "idle_window_seconds": IDLE_WINDOW_SECONDS,
            "idle_poll_seconds": IDLE_POLL_SECONDS,
            "idle_max_seconds": IDLE_MAX_SECONDS,
            "sqlite_statement_text_retained": False,
            "rss_semantics": "process_peak_rss",
            "wakeup_semantics": "voluntary_plus_involuntary_context_switch_delta",
            "optimization_performed": True,
            "g6_01_authorized": True,
            "g6_02_authorized": True,
            "session_run_update_scale": RUN_UPDATE_SCALE,
            "accepted_budgets": {
                "worker_idle_loop": {
                    "projected_steady_cycles_per_minute_max": (
                        MAX_IDLE_CYCLES_PER_MINUTE
                    )
                },
                "worker_wakeup_signal": {
                    "wall_p95_ms_max": MAX_WAKE_LATENCY_MS,
                    "delivered_p95_min": 1.0,
                },
            },
        },
        "results": results,
        "ranked_bottlenecks": ranked,
        "candidate_repairs": [
            {
                "id": "demand_driven_worker_wakeup",
                "evidence": [
                    "worker_idle_loop",
                    "worker_idle_cycle",
                    "worker_wakeup_signal",
                ],
                "status": "implemented_within_budget",
            },
            {
                "id": "conflict_aware_worker_concurrency",
                "evidence": [
                    "queue_claim_many",
                    "sqlite_lock_contention",
                    "worker_active_echo",
                ],
                "status": "not_selected_by_G6-01",
            },
            {
                "id": "ranked_request_hot_path_repairs",
                "evidence": [
                    "worker_active_echo",
                    "session_run_update",
                    "api_defaults",
                    "api_session_events",
                    "sse_terminal_attach",
                    "tui_navigation_load",
                ],
                "status": "bounded_filesystem_scan_repair_implemented",
            },
        ],
        "missing_coverage": missing,
        "runtime_scaling_baseline": runtime_scaling_baseline,
        "status": (
            "passed"
            if not missing
            and all(
                result["target_status"] == "within_target"
                for result in accepted_results
            )
            else "failed"
        ),
    }


def _profile_once(root: Path) -> list[_OperationSample]:
    from .runtime_cases import _profile_queue_and_lifecycle
    from .runtime_storage import _profile_session_storage, _profile_worker_resources
    from .runtime_surfaces import _profile_request_path

    observations = _profile_session_storage(root / "sessions")
    observations.extend(_profile_worker_resources(root / "workers"))
    observations.extend(_profile_queue_and_lifecycle(root / "queue"))
    observations.extend(_profile_request_path(root / "requests"))
    return observations


def _summarize(
    operation_id: str,
    samples: list[_OperationSample],
) -> dict[str, Any]:
    from .runtime_metrics import _summarize as summarize

    return summarize(operation_id, samples)
