"""Bounded content-free facts read from existing product owners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


MAX_PRODUCT_SESSIONS = 256
MAX_PRODUCT_RUNS = 2_048
MAX_PRODUCT_JOBS = 2_048
MAX_PRODUCT_APPROVALS = 200
MAX_PRODUCT_POLICY_EVENTS = 1_000
MAX_PRODUCT_DELIVERIES = 1_024

_PRODUCT_SOURCES = frozenset(
    {
        "approvals",
        "attempts",
        "gateway",
        "jobs",
        "project",
        "review",
        "runs",
        "sessions",
        "sidecar",
        "thread_relay",
    }
)


class ProductSessionOwner(Protocol):
    """Bounded session reads used by the evidence projection."""

    def list_sessions(self, **kwargs: Any) -> tuple[Any, ...]: ...

    def list_runs_page(self, session_id: str, **kwargs: Any) -> Any: ...


class ProductRuntimeOwner(Protocol):
    """Bounded runtime reads used by the evidence projection."""

    def list_jobs_page(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]: ...

    def list_attempts(self, job_id: str | None = None) -> tuple[Any, ...]: ...

    def list_approval_requests(self, **kwargs: Any) -> tuple[Any, ...]: ...

    def list_policy_audit_events(self, **kwargs: Any) -> tuple[Any, ...]: ...


@dataclass(frozen=True, slots=True)
class GatewayProductFactV1:
    """Content-free gateway outcome supplied by its existing runtime owner."""

    preflight_status: str
    support_status: str
    sidecar_attach: str | None = None

    def __post_init__(self) -> None:
        if self.preflight_status not in {"ready", "blocked"}:
            raise ValueError("gateway product preflight status is invalid")
        if self.support_status not in {
            "stable",
            "technical_preview",
            "vendor_unsupported",
            "blocked",
        }:
            raise ValueError("gateway product support status is invalid")
        if self.sidecar_attach not in {None, "cold", "warm"}:
            raise ValueError("gateway sidecar attach fact is invalid")


@dataclass(frozen=True, slots=True)
class ProductEvidenceSnapshotV1:
    """Bounded owner facts; no event message, prompt, or response content."""

    project_created_at: str | None = None
    sessions: tuple[Any, ...] = ()
    runs: tuple[Any, ...] = ()
    jobs: tuple[Any, ...] = ()
    attempts: tuple[Any, ...] = ()
    approvals: tuple[Any, ...] = ()
    policy_events: tuple[Any, ...] = ()
    deliveries: tuple[Any, ...] = ()
    gateway_facts: tuple[GatewayProductFactV1, ...] = ()
    available_sources: tuple[str, ...] = ()
    truncated_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        sources = tuple(sorted(set(self.available_sources)))
        truncated = tuple(sorted(set(self.truncated_sources)))
        if len(sources) != len(self.available_sources):
            raise ValueError("product evidence available sources must be unique")
        if len(truncated) != len(self.truncated_sources):
            raise ValueError("product evidence truncated sources must be unique")
        if not set(truncated).issubset(sources):
            raise ValueError("truncated product source must be available")
        if not set(sources).issubset(_PRODUCT_SOURCES):
            raise ValueError("product evidence source is unsupported")
        if (self.project_created_at is None) != ("project" not in sources):
            raise ValueError("project source requires its retained creation time")
        if self.project_created_at is not None:
            _timestamp(self.project_created_at)
        object.__setattr__(self, "available_sources", sources)
        object.__setattr__(self, "truncated_sources", truncated)
        for values, maximum, label in (
            (self.sessions, MAX_PRODUCT_SESSIONS, "sessions"),
            (self.runs, MAX_PRODUCT_RUNS, "runs"),
            (self.jobs, MAX_PRODUCT_JOBS, "jobs"),
            (self.attempts, MAX_PRODUCT_RUNS, "attempts"),
            (self.approvals, MAX_PRODUCT_APPROVALS, "approvals"),
            (self.policy_events, MAX_PRODUCT_POLICY_EVENTS, "review"),
            (self.deliveries, MAX_PRODUCT_DELIVERIES, "thread_relay"),
            (self.gateway_facts, MAX_PRODUCT_RUNS, "gateway"),
        ):
            if not isinstance(values, tuple) or len(values) > maximum:
                raise ValueError(f"product evidence {label} facts exceed bounds")


def collect_owner_product_facts(
    *,
    project_id: str,
    range_start: datetime,
    range_end: datetime,
    session_owner: ProductSessionOwner,
    runtime_owner: ProductRuntimeOwner,
    project_created_at: str | None = None,
) -> ProductEvidenceSnapshotV1:
    """Read bounded metadata records from session and runtime owners."""
    sessions_page = session_owner.list_sessions(
        project_id=project_id,
        include_archived=True,
        limit=MAX_PRODUCT_SESSIONS + 1,
    )
    sessions = tuple(
        item
        for item in sessions_page[:MAX_PRODUCT_SESSIONS]
        if _in_range(item.created_at, range_start, range_end)
        or _in_range(item.updated_at, range_start, range_end)
    )
    truncated: list[str] = []
    if len(sessions_page) > MAX_PRODUCT_SESSIONS:
        truncated.append("sessions")

    runs: list[Any] = []
    runs_truncated = False
    for session in sessions:
        cursor = None
        while len(runs) < MAX_PRODUCT_RUNS:
            page = session_owner.list_runs_page(session.id, cursor=cursor, limit=100)
            for run in page.items:
                if _run_in_range(run, range_start, range_end):
                    runs.append(run)
                    if len(runs) == MAX_PRODUCT_RUNS:
                        break
            if not page.has_more or page.next_cursor is None:
                break
            cursor = page.next_cursor
        if len(runs) == MAX_PRODUCT_RUNS:
            runs_truncated = True
            break
    if runs_truncated:
        truncated.append("runs")

    jobs: list[Any] = []
    cursor = None
    has_more = False
    while len(jobs) < MAX_PRODUCT_JOBS:
        page, has_more = runtime_owner.list_jobs_page(
            project_id=project_id,
            cursor=cursor,
            limit=100,
        )
        for job in page:
            if _record_in_range(job, range_start, range_end):
                jobs.append(job)
                if len(jobs) == MAX_PRODUCT_JOBS:
                    break
        if not has_more or not page:
            break
        cursor = (page[-1].created_at, page[-1].id)
    if len(jobs) == MAX_PRODUCT_JOBS and has_more:
        truncated.append("jobs")

    attempts = tuple(
        attempt
        for job in jobs
        for attempt in runtime_owner.list_attempts(job.id)
        if _record_in_range(attempt, range_start, range_end)
    )[:MAX_PRODUCT_RUNS]
    if len(attempts) == MAX_PRODUCT_RUNS:
        truncated.append("attempts")

    approvals_page = runtime_owner.list_approval_requests(limit=MAX_PRODUCT_APPROVALS)
    approvals = tuple(
        item
        for item in approvals_page
        if item.project_id == project_id
        and _approval_in_range(item, range_start, range_end)
    )
    if len(approvals_page) == MAX_PRODUCT_APPROVALS:
        truncated.append("approvals")

    policy_page = runtime_owner.list_policy_audit_events(
        limit=MAX_PRODUCT_POLICY_EVENTS
    )
    policy_events = tuple(
        item
        for item in policy_page
        if item.project_id == project_id
        and _in_range(item.created_at, range_start, range_end)
    )
    if len(policy_page) == MAX_PRODUCT_POLICY_EVENTS:
        truncated.append("review")

    return ProductEvidenceSnapshotV1(
        project_created_at=project_created_at,
        sessions=sessions,
        runs=tuple(runs),
        jobs=tuple(jobs),
        attempts=attempts,
        approvals=approvals,
        policy_events=policy_events,
        available_sources=(
            "approvals",
            "attempts",
            "jobs",
            *(("project",) if project_created_at is not None else ()),
            "review",
            "runs",
            "sessions",
        ),
        truncated_sources=tuple(truncated),
    )


def _record_in_range(item: Any, start: datetime, end: datetime) -> bool:
    return _in_range(item.created_at, start, end) or _in_range(
        item.updated_at, start, end
    )


def _run_in_range(item: Any, start: datetime, end: datetime) -> bool:
    return _record_in_range(item, start, end) or (
        item.finished_at is not None and _in_range(item.finished_at, start, end)
    )


def _approval_in_range(item: Any, start: datetime, end: datetime) -> bool:
    return _in_range(item.created_at, start, end) or (
        item.decided_at is not None and _in_range(item.decided_at, start, end)
    )


def _in_range(value: str, start: datetime, end: datetime) -> bool:
    parsed = _timestamp(value)
    return start <= parsed <= end


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("product evidence source timestamps must be timezone-aware")
    return parsed


__all__ = [
    "GatewayProductFactV1",
    "ProductEvidenceSnapshotV1",
    "collect_owner_product_facts",
]
