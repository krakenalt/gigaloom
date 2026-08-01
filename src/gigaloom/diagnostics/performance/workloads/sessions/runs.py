"""Session run update workload."""

from pathlib import Path
from typing import Final

from gigaloom.diagnostics.performance.workloads import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.sessions.cases import (
    CaseFactory,
    StorageCase,
)
from gigaloom.diagnostics.performance.workloads.sessions.fixtures import (
    build_history_fixture,
)

RUN_SCALES: Final[tuple[int, ...]] = (10, 100, 1_000)

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
        future_gate="performance-regression-followup",
    ),
)


def case_factories() -> tuple[CaseFactory, ...]:
    """Build warm-index O(total-runs) update cases."""
    return tuple(_update_case(scale) for scale in RUN_SCALES)


def _update_case(scale: int) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        fixture = build_history_fixture(root, runs=scale)
        run_id = fixture.run_ids[-1]
        fixture.store.get_run(run_id)

        def operation(_counters):
            updated = fixture.store.update_run(run_id, status="succeeded")
            return {
                "retained_runs": scale,
                "updated_runs": int(updated.id == run_id),
            }

        return StorageCase(
            id=f"sessions.runs.update_1_of_{scale}",
            family="sessions/runs",
            fixture={"runs": scale, "read_index": "warm"},
            operation=operation,
        )

    return prepare
