"""Runtime revision query workload."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from gigaloom.diagnostics.performance.workloads import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.runtime.contracts import (
    RuntimeCase,
    RuntimeCaseFactory,
)
from gigaloom.diagnostics.performance.workloads.runtime.fixtures import (
    seed_revision_rows,
)
from gigaloom.diagnostics.performance.workloads.runtime.instrumentation import (
    RuntimeCounters,
    TracingRuntimeStore,
)


REVISION_SCALES: Final[tuple[int, ...]] = (100, 1_000, 10_000, 50_000)
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
        future_gate="performance-regression-followup",
    ),
)


def case_factories() -> tuple[RuntimeCaseFactory, ...]:
    """Return runs-center revision cases at every required scale."""
    return tuple(_revision_factory(scale) for scale in REVISION_SCALES)


def _revision_factory(scale: int) -> RuntimeCaseFactory:
    def factory(root: Path) -> RuntimeCase:
        store = TracingRuntimeStore(root)
        seed_revision_rows(store, count=scale)

        def operation(counters: RuntimeCounters) -> dict[str, int]:
            revision = store.runs_center_revision()
            if len(revision) != 64:
                raise RuntimeError("runs-center revision fixture returned invalid hash")
            counters.rows_parsed = scale
            return {"rows_scanned": scale, "revision_bytes": len(revision) // 2}

        return RuntimeCase(
            id=f"runtime.revisions.runs_center.rows_{scale}",
            family="runtime/revisions",
            fixture={"rows": scale},
            store=store,
            operation=operation,
        )

    return factory
