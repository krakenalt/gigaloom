"""Session message history workload."""

from pathlib import Path
from typing import Final

from gpt2giga_harness.diagnostics.performance.workloads import WorkloadSpec
from gpt2giga_harness.diagnostics.performance.workloads.sessions.cases import (
    CaseFactory,
    StorageCase,
)
from gpt2giga_harness.diagnostics.performance.workloads.sessions.fixtures import (
    build_history_fixture,
)


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


def case_factories() -> tuple[CaseFactory, ...]:
    """Build the legacy full-scan latest-message case."""

    def prepare(root: Path) -> StorageCase:
        fixture = build_history_fixture(root, messages=5_000)

        def operation(_counters):
            latest = fixture.store.list_messages(fixture.session_id)[-20:]
            return {"returned_messages": len(latest)}

        return StorageCase(
            id="sessions.messages.latest_20_of_5000",
            family="sessions/messages",
            fixture={"messages": 5_000, "returned": 20},
            operation=operation,
        )

    return (prepare,)
