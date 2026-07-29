"""Session catalog scaling workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.catalog.scaling",
        family="sessions/catalog",
        profiles=("local-detail",),
        variants=(
            "create_10",
            "create_100",
            "create_1000",
            "delete",
            "cold_first_page",
            "warm_first_page",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "bytes_read",
            "bytes_written",
            "files_opened",
            "index_reads",
            "atomic_replaces",
            "fsync_calls",
        ),
        future_gate="G-PERF",
    ),
)
