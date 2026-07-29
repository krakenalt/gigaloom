"""Session run update workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.runs.update",
        family="sessions/runs",
        profiles=("local-detail", "runtime-detail"),
        variants=("update_1_of_10", "update_1_of_100", "update_1_of_1000"),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "bytes_read",
            "bytes_written",
            "files_opened",
            "rows_parsed",
            "atomic_replaces",
            "fsync_calls",
        ),
        future_gate="G-PERF",
    ),
)
