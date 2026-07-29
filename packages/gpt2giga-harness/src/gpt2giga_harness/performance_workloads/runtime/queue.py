"""Runtime queue claim workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


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
