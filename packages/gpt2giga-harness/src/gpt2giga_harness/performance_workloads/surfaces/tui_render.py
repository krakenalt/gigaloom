"""TUI projection and bounded render workload."""

from typing import Final

from gpt2giga_harness.performance_workloads import WorkloadSpec


WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="tui.render.timeline",
        family="tui/render",
        profiles=("tui-detail",),
        variants=(
            "startup_to_paint",
            "first_input_to_paint",
            "timeline_full",
            "timeline_incremental",
            "unchanged_snapshot",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "events_projected",
            "widgets_created",
            "widgets_updated",
            "rerenders",
        ),
        future_gate="G-PERF",
    ),
)
