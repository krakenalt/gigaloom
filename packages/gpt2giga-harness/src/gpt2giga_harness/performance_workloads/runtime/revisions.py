"""Runtime revision query workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="runtime.revisions.runs_center",
        family="runtime/revisions",
        profiles=("runtime-detail",),
        variants=("rows_100", "rows_1000", "rows_10000", "rows_50000"),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "sqlite_connections",
            "sqlite_statements",
            "rows_parsed",
        ),
        future_gate="G-PERF",
    ),
)
