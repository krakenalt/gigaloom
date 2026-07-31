"""Session catalog scaling workload."""

from pathlib import Path
from typing import Final

from gigaloom.diagnostics.performance.workloads import WorkloadSpec
from gigaloom.diagnostics.performance.workloads.sessions.cases import (
    CaseFactory,
    StorageCase,
)
from gigaloom.diagnostics.performance.workloads.sessions.fixtures import (
    SessionFixture,
    build_catalog_fixture,
)

CATALOG_SCALES: Final[tuple[int, ...]] = (10, 100, 1_000)

WORKLOADS: Final[tuple[WorkloadSpec, ...]] = (
    WorkloadSpec(
        id="sessions.catalog.scaling",
        family="sessions/catalog",
        profiles=("local-detail",),
        variants=(
            "create_10",
            "create_100",
            "create_1000",
            "delete",
            "cold_first_page",
            "warm_first_page",
        ),
        required_metrics=("wall_ms", "cpu_ms", "rss_bytes"),
        required_counters=(
            "bytes_read",
            "bytes_written",
            "files_opened",
            "index_reads",
            "atomic_replaces",
            "fsync_calls",
        ),
        future_gate="performance-regression-followup",
    ),
)


def case_factories() -> tuple[CaseFactory, ...]:
    """Build isolated create/delete and cold/warm page cases."""
    factories: list[CaseFactory] = []
    for scale in CATALOG_SCALES:
        factories.extend(
            (
                _create_case(scale),
                _delete_case(scale),
                _cold_page_case(scale),
                _warm_page_case(scale),
            )
        )
    return tuple(factories)


def _create_case(scale: int) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        state = {"fixture": build_catalog_fixture(root, scale)}

        def operation(_counters):
            created = state["fixture"].store.create_session(title="content-free")
            return {"sessions_after": scale + 1, "created": int(bool(created.id))}

        def reset() -> None:
            state["fixture"] = build_catalog_fixture(root, scale)

        return StorageCase(
            id=f"sessions.catalog.create_{scale}",
            family="sessions/catalog",
            fixture={"sessions_before": scale, "operation": "create_one"},
            operation=operation,
            reset=reset,
        )

    return prepare


def _delete_case(scale: int) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        state = {"fixture": build_catalog_fixture(root, scale)}

        def operation(_counters):
            fixture = state["fixture"]
            fixture.store.delete_session(fixture.session_ids[-1])
            return {"sessions_after": scale - 1, "deleted": 1}

        def reset() -> None:
            state["fixture"] = build_catalog_fixture(root, scale)

        return StorageCase(
            id=f"sessions.catalog.delete_{scale}",
            family="sessions/catalog",
            fixture={"sessions_before": scale, "operation": "delete_one"},
            operation=operation,
            reset=reset,
        )

    return prepare


def _cold_page_case(scale: int) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        state = {"fixture": build_catalog_fixture(root, scale)}

        def operation(_counters):
            page = state["fixture"].store.list_sessions_page(limit=20)
            return {"items": len(page.items), "has_more": int(page.has_more)}

        def reset() -> None:
            state["fixture"] = build_catalog_fixture(root, scale)

        return StorageCase(
            id=f"sessions.catalog.first_page_cold_{scale}",
            family="sessions/catalog",
            fixture={"sessions": scale, "read_index": "absent", "page_size": 20},
            operation=operation,
            reset=reset,
        )

    return prepare


def _warm_page_case(scale: int) -> CaseFactory:
    def prepare(root: Path) -> StorageCase:
        fixture: SessionFixture = build_catalog_fixture(root, scale)
        fixture.store.list_sessions_page(limit=20)

        def operation(_counters):
            page = fixture.store.list_sessions_page(limit=20)
            return {"items": len(page.items), "has_more": int(page.has_more)}

        return StorageCase(
            id=f"sessions.catalog.first_page_warm_{scale}",
            family="sessions/catalog",
            fixture={"sessions": scale, "read_index": "warm", "page_size": 20},
            operation=operation,
        )

    return prepare
