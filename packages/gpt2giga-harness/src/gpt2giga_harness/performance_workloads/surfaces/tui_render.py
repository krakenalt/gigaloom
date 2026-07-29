"""TUI projection and bounded render workload."""

from __future__ import annotations

import statistics
import time
from typing import Any, Final

from gpt2giga_harness.performance_workloads import WorkloadSpec
from gpt2giga_harness.tui.client import TimelineEvent
from gpt2giga_harness.tui.widgets.timeline import (
    TimelinePanel,
    TimelineRenderCounters,
)


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
            "timeline_navigation",
            "timeline_history_10000",
            "unchanged_snapshot",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "events_inspected",
            "cards_rendered",
            "chars_produced",
            "widget_updates",
        ),
        future_gate="G-PERF",
    ),
)


def profile_timeline_projections() -> tuple[
    dict[str, float],
    dict[str, TimelineRenderCounters],
]:
    """Measure bounded full, incremental, navigation, and long-history work."""
    labels = {"message": "MESSAGE"}
    full_10 = _events(0, 10)
    full_100 = _events(0, 100)
    full_10_000 = _events(0, 10_000)
    incremental_1 = (*full_100[1:], *_events(100, 1))
    batch_10 = (*full_100[10:], *_events(100, 10))

    def measure_set(
        initial: tuple[TimelineEvent, ...],
        events: tuple[TimelineEvent, ...],
    ) -> tuple[float, TimelineRenderCounters]:
        panel = TimelinePanel("empty", labels, id="timeline")
        panel.set_events(initial)
        started = time.perf_counter_ns()
        panel.set_events(events)
        return _elapsed_ms(started), panel.last_render_counters

    def measure_navigation() -> tuple[float, TimelineRenderCounters]:
        panel = TimelinePanel("empty", labels, id="timeline")
        panel.set_events(full_100)
        started = time.perf_counter_ns()
        panel.action_previous_card()
        return _elapsed_ms(started), panel.last_render_counters

    observations = {
        "timeline_full_10_projection": measure_set((), full_10),
        "timeline_full_100_projection": measure_set((), full_100),
        "timeline_incremental_1_projection": measure_set(full_100, incremental_1),
        "timeline_batch_10_projection": measure_set(full_100, batch_10),
        "timeline_navigation_1_projection": measure_navigation(),
        "timeline_full_10000_projection": measure_set((), full_10_000),
    }
    return (
        {name: observation[0] for name, observation in observations.items()},
        {name: observation[1] for name, observation in observations.items()},
    )


def summarize_render_workloads(
    samples: list[dict[str, TimelineRenderCounters]],
) -> list[dict[str, Any]]:
    """Summarize stable algorithmic counters across repeated samples."""
    names = sorted(samples[0])
    return [
        {
            "id": name,
            "family": "tui/render",
            "samples": len(samples),
            "measured_window": "operation_only",
            "regression_gate": {
                "blocking": False,
                "classification": "reference_wall_time_algorithmic_counters",
            },
            "counters": {
                counter: _counter_summary(
                    [float(getattr(sample[name], counter)) for sample in samples]
                )
                for counter in (
                    "events_inspected",
                    "cards_rendered",
                    "chars_produced",
                    "widget_updates",
                )
            },
        }
        for name in names
    ]


def _events(offset: int, count: int) -> tuple[TimelineEvent, ...]:
    return tuple(
        TimelineEvent(
            id=f"event_{offset + index:04d}",
            type="message_delta",
            message=f"content-free event {offset + index:04d}",
            delta="x" * 80,
            category="message",
        )
        for index in range(count)
    )


def _counter_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "p99": _percentile(ordered, 99),
        "mean": round(statistics.fmean(ordered), 3),
        "max": round(max(ordered), 3),
    }


def _percentile(values: list[float], percentile: int) -> float:
    rank = (len(values) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(values) - 1)
    weight = rank - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 3)


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000
