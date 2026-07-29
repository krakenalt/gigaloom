"""Bounded Web read-path workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="web.read_paths.initial",
        family="web/read-paths",
        profiles=("local-detail", "runtime-detail"),
        variants=(
            "overview",
            "environment",
            "messages",
            "runs",
            "attachments",
            "events",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "requests",
            "bytes_read",
            "files_opened",
            "sqlite_connections",
            "sqlite_statements",
        ),
        future_gate="G-PERF",
    ),
)
