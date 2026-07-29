"""Worker lifecycle and maintenance workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="runtime.worker.lifecycle",
        family="runtime/worker",
        profiles=("runtime-detail",),
        variants=(
            "heartbeat",
            "idle",
            "schedule",
            "recovery",
            "reconcile",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "wakeups",
            "sqlite_connections",
            "sqlite_statements",
            "maintenance_cycles",
        ),
        future_gate="G-PERF",
    ),
)
