"""Session event append and query workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.events.persistence",
        family="sessions/events",
        profiles=("local-detail", "runtime-detail"),
        variants=(
            "tail_50000",
            "page_50000",
            "direct_lookup_50000",
            "steady_100_per_second",
            "burst_500",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "bytes_read",
            "bytes_written",
            "files_opened",
            "rows_parsed",
            "fsync_calls",
        ),
        future_gate="G-PERF",
    ),
)
