"""Session message history workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.messages.tail",
        family="sessions/messages",
        profiles=("local-detail",),
        variants=("tail_20_of_5000", "first_page", "next_page"),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=("bytes_read", "files_opened", "rows_parsed"),
        future_gate="G-PERF",
    ),
)
