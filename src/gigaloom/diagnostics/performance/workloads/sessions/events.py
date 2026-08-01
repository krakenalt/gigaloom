"""Session event append and query workload."""

from pathlib import Path
from typing import Final

from gigaloom.diagnostics.performance.workloads import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.sessions.cases import (
    CaseFactory,
    StorageCase,
)
from gigaloom.diagnostics.performance.workloads.sessions.fixtures import (
    build_history_fixture,
    fixture_event,
)

EVENT_HISTORY_SIZE: Final[int] = 50_000

WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.events.persistence",
        family="sessions/events",
        profiles=("local-detail", "runtime-detail"),
        variants=(
            "tail_50000",
            "page_50000",
            "direct_lookup_50000",
            "steady_100_per_second",
            "burst_500",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "bytes_read",
            "bytes_written",
            "files_opened",
            "rows_parsed",
            "fsync_calls",
        ),
        future_gate="performance-regression-followup",
    ),
)


def case_factories() -> tuple[CaseFactory, ...]:
    """Build bounded read, direct lookup and append-pressure cases."""
    return (
        _tail_case(),
        _page_case(),
        _direct_lookup_case(),
        _append_case(100, steady=True),
        _append_case(500, steady=False),
    )


def _tail_case() -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        fixture = build_history_fixture(root, events=EVENT_HISTORY_SIZE)
        offset = fixture.event_offsets[-20]

        def operation(counters):
            page = fixture.store.list_event_tail_page(
                fixture.session_id,
                run_id=fixture.run_ids[0],
                offset=offset,
                limit=20,
            )
            counters.record_direct_read(
                byte_count=page.next_offset - offset,
                rows=len(page.items),
            )
            return {"returned_events": len(page.items), "has_more": int(page.has_more)}

        return StorageCase(
            id="sessions.events.tail_20_of_50000",
            family="sessions/events",
            fixture={"events": EVENT_HISTORY_SIZE, "returned": 20},
            operation=operation,
        )

    return prepare


def _page_case() -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        fixture = build_history_fixture(root, events=EVENT_HISTORY_SIZE)

        def operation(counters):
            page = fixture.store.list_record_page(
                fixture.session_id,
                record_type="events",
                projector=lambda event: {"id": event.id, "type": event.type},
                limit=100,
            )
            counters.record_direct_read(
                byte_count=page.next_offset or page.byte_count,
                rows=len(page.items),
            )
            return {"returned_events": len(page.items), "has_more": int(page.has_more)}

        return StorageCase(
            id="sessions.events.page_100_of_50000",
            family="sessions/events",
            fixture={"events": EVENT_HISTORY_SIZE, "page_size": 100},
            operation=operation,
        )

    return prepare


def _direct_lookup_case() -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        fixture = build_history_fixture(root, events=EVENT_HISTORY_SIZE)
        event_id = fixture.event_ids[-1]

        def operation(counters):
            cursor = fixture.store.resolve_event_cursor(
                fixture.session_id,
                run_id=fixture.run_ids[0],
                event_id=event_id,
            )
            if cursor is None:
                raise RuntimeError("fixture event cursor was not resolved")
            counters.record_direct_read(
                byte_count=cursor.offset,
                rows=EVENT_HISTORY_SIZE,
            )
            return {"found": 1, "cursor_offset": cursor.offset}

        return StorageCase(
            id="sessions.events.direct_lookup_last_of_50000",
            family="sessions/events",
            fixture={"events": EVENT_HISTORY_SIZE, "target": "last"},
            operation=operation,
        )

    return prepare


def _append_case(event_count: int, *, steady: bool) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        state = {"fixture": build_history_fixture(root, runs=1)}

        def operation(_counters):
            fixture = state["fixture"]
            for index in range(event_count):
                fixture.store.append_event(
                    fixture_event(fixture.session_id, fixture.run_ids[0], index)
                )
            return {"appended_events": event_count}

        def reset() -> None:
            state["fixture"] = build_history_fixture(root, runs=1)

        suffix = "steady_100" if steady else "burst_500"
        fixture_contract: dict[str, int | str | bool] = {
            "events": event_count,
            "mode": "steady_capacity" if steady else "burst",
        }
        if steady:
            fixture_contract["target_rate_per_second"] = 100
        return StorageCase(
            id=f"sessions.events.append_{suffix}",
            family="sessions/events",
            fixture=fixture_contract,
            operation=operation,
            reset=reset,
        )

    return prepare
