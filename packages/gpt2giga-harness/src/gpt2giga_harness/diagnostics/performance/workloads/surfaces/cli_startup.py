"""Isolated CLI startup workload."""

from typing import Final

from gpt2giga_harness.diagnostics.performance.workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="cli.startup.isolated",
        family="cli/startup",
        profiles=("local-detail",),
        variants=("version_cold", "version_warm", "help_cold", "help_warm"),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=("modules_loaded", "subprocesses_started"),
        future_gate="G-PERF",
    ),
)
