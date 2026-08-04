"""Unknown-safe aggregates over existing content-free owner records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from statistics import median
from typing import Any, Protocol

from gigaloom.contracts.product_evidence import (
    ProductEvidenceReportV1,
    ProductEvidenceSourceV1,
    ProductMetricStatus,
    ProductMetricUnit,
    ProductMetricV1,
)
from gigaloom.review.ports import (
    PolicyAuditPhase,
    REVIEWED_PROMOTION_APPLY_OWNER,
    REVIEWED_PROMOTION_BRANCH_OWNER,
    REVIEWED_PROMOTION_MERGE_OWNER,
)
from gigaloom.runtime.api import ApprovalStatus, JobAttemptStatus, RunStatus


MAX_PRODUCT_SESSIONS = 256
MAX_PRODUCT_RUNS = 2_048
MAX_PRODUCT_JOBS = 2_048
MAX_PRODUCT_APPROVALS = 200
MAX_PRODUCT_POLICY_EVENTS = 1_000
MAX_PRODUCT_DELIVERIES = 1_024

_REVIEW_OWNERS = frozenset(
    {
        REVIEWED_PROMOTION_APPLY_OWNER,
        REVIEWED_PROMOTION_BRANCH_OWNER,
        REVIEWED_PROMOTION_MERGE_OWNER,
    }
)
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
class _ProjectFact:
    created_at: str


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


def build_product_evidence_report(
    *,
    project_id: str,
    range_start: datetime,
    range_end: datetime,
    generated_at: datetime,
    snapshot: ProductEvidenceSnapshotV1,
) -> ProductEvidenceReportV1:
    """Compute deterministic aggregate metrics without mining event content."""
    project_facts = (
        [_ProjectFact(snapshot.project_created_at)]
        if snapshot.project_created_at is not None
        else []
    )
    sessions = sorted(snapshot.sessions, key=lambda item: (item.created_at, item.id))
    runs = sorted(snapshot.runs, key=lambda item: (item.created_at, item.id))
    jobs = sorted(snapshot.jobs, key=lambda item: (item.created_at, item.id))
    approvals = sorted(snapshot.approvals, key=lambda item: (item.created_at, item.id))
    accepted = tuple(
        event
        for event in snapshot.policy_events
        if event.phase is PolicyAuditPhase.ENFORCEMENT
        and event.enforcement_owner in _REVIEW_OWNERS
    )

    metrics = [
        _duration_from_first(
            "activation.project_to_first_success",
            project_facts,
            [run for run in runs if run.status is RunStatus.SUCCEEDED],
            start_attr="created_at",
            finish_attrs=("finished_at", "updated_at"),
            sources=snapshot.available_sources,
            required=("project", "runs"),
        ),
        _duration_from_first(
            "activation.project_to_first_accepted_change",
            project_facts,
            list(accepted),
            start_attr="created_at",
            finish_attrs=("created_at",),
            sources=snapshot.available_sources,
            required=("project", "review"),
        ),
        _first_run_status(runs, snapshot.available_sources),
        _ratio(
            "runs.success_ratio",
            sum(run.status is RunStatus.SUCCEEDED for run in runs),
            len(runs),
            "runs" in snapshot.available_sources,
        ),
        _approval_latency(approvals, snapshot.available_sources),
        _count(
            "approvals.abandoned",
            sum(
                item.status in {ApprovalStatus.EXPIRED, ApprovalStatus.CANCELED}
                for item in approvals
            ),
            len(approvals),
            "approvals" in snapshot.available_sources,
        ),
        _count(
            "interventions.cancellations",
            sum(job.cancel_requested_at is not None for job in jobs),
            len(jobs),
            "jobs" in snapshot.available_sources,
        ),
        _count(
            "recovery.interrupted_or_retried",
            sum(
                attempt.status is JobAttemptStatus.INTERRUPTED
                or attempt.retry_reason is not None
                for attempt in snapshot.attempts
            ),
            len(snapshot.attempts),
            "attempts" in snapshot.available_sources,
        ),
        _count(
            "review.accepted_changes",
            len(accepted),
            len(snapshot.policy_events),
            "review" in snapshot.available_sources,
        ),
        _count(
            "usage.work",
            sum(job.origin == "manual" for job in jobs),
            len(jobs),
            "jobs" in snapshot.available_sources,
        ),
        _count(
            "usage.inbox",
            len(approvals),
            len(approvals),
            "approvals" in snapshot.available_sources,
        ),
        _count(
            "usage.automations",
            sum(
                job.schedule_id is not None
                or job.workflow_id is not None
                or job.origin != "manual"
                for job in jobs
            ),
            len(jobs),
            "jobs" in snapshot.available_sources,
        ),
        _count(
            "usage.library",
            len(sessions),
            len(sessions),
            "sessions" in snapshot.available_sources,
        ),
        _count(
            "relay.deliveries",
            len(snapshot.deliveries),
            len(snapshot.deliveries),
            "thread_relay" in snapshot.available_sources,
        ),
        _ratio(
            "relay.completed_ratio",
            sum(
                item.receipt.status.value == "completed" for item in snapshot.deliveries
            ),
            len(snapshot.deliveries),
            "thread_relay" in snapshot.available_sources,
        ),
        _count(
            "relay.rejected",
            sum(
                item.receipt.status.value in {"failed", "expired", "cancelled"}
                for item in snapshot.deliveries
            ),
            len(snapshot.deliveries),
            "thread_relay" in snapshot.available_sources,
        ),
        _ratio(
            "gateway.preflight_success_ratio",
            sum(item.preflight_status == "ready" for item in snapshot.gateway_facts),
            len(snapshot.gateway_facts),
            "gateway" in snapshot.available_sources,
        ),
        _count(
            "gateway.unsupported_routes",
            sum(item.support_status == "blocked" for item in snapshot.gateway_facts),
            len(snapshot.gateway_facts),
            "gateway" in snapshot.available_sources,
        ),
        _count(
            "gateway.sidecar_cold_attach",
            sum(item.sidecar_attach == "cold" for item in snapshot.gateway_facts),
            len(snapshot.gateway_facts),
            "sidecar" in snapshot.available_sources,
        ),
        _count(
            "gateway.sidecar_warm_attach",
            sum(item.sidecar_attach == "warm" for item in snapshot.gateway_facts),
            len(snapshot.gateway_facts),
            "sidecar" in snapshot.available_sources,
        ),
    ]
    sources = tuple(
        ProductEvidenceSourceV1(
            source_id=source,
            observed_count=_source_count(snapshot, source),
            truncated=source in snapshot.truncated_sources,
        )
        for source in snapshot.available_sources
    )
    omissions = tuple(
        source
        for source in ("gateway", "sidecar", "thread_relay")
        if source not in snapshot.available_sources
    ) + tuple(f"truncated.{source}" for source in snapshot.truncated_sources)
    report_id = (
        "product-beta-"
        + _report_identity(
            project_id,
            range_start,
            range_end,
            generated_at,
            metrics,
        )[:24]
    )
    return ProductEvidenceReportV1(
        report_id=report_id,
        project_id=project_id,
        range_start=range_start,
        range_end=range_end,
        generated_at=generated_at,
        metrics=tuple(metrics),
        sources=sources,
        omissions=omissions,
    )


def _duration_from_first(
    metric_id: str,
    starts: list[Any],
    finishes: list[Any],
    *,
    start_attr: str,
    finish_attrs: tuple[str, ...],
    sources: tuple[str, ...],
    required: tuple[str, ...],
) -> ProductMetricV1:
    if not set(required).issubset(sources):
        return _unknown(metric_id, ProductMetricUnit.DURATION_MS, "source_unavailable")
    if not starts or not finishes:
        return _unknown(
            metric_id, ProductMetricUnit.DURATION_MS, "milestone_not_observed"
        )
    started = _timestamp(getattr(starts[0], start_attr))
    candidates = [
        parsed
        for item in finishes
        if (parsed := _first_timestamp(item, finish_attrs)) is not None
        and parsed >= started
    ]
    if not candidates:
        return _unknown(
            metric_id, ProductMetricUnit.DURATION_MS, "milestone_not_observed"
        )
    return _observed(
        metric_id,
        ProductMetricUnit.DURATION_MS,
        int((min(candidates) - started).total_seconds() * 1_000),
        sample_size=1,
    )


def _first_run_status(runs: list[Any], sources: tuple[str, ...]) -> ProductMetricV1:
    if "runs" not in sources:
        return _unknown(
            "runs.first_status", ProductMetricUnit.STATUS, "source_unavailable"
        )
    if not runs:
        return _unknown(
            "runs.first_status", ProductMetricUnit.STATUS, "run_not_observed"
        )
    return _observed(
        "runs.first_status",
        ProductMetricUnit.STATUS,
        runs[0].status.value,
        sample_size=1,
    )


def _approval_latency(
    approvals: list[Any], sources: tuple[str, ...]
) -> ProductMetricV1:
    if "approvals" not in sources:
        return _unknown(
            "approvals.decision_latency",
            ProductMetricUnit.DURATION_MS,
            "source_unavailable",
        )
    values = [
        int(
            (_timestamp(item.decided_at) - _timestamp(item.created_at)).total_seconds()
            * 1_000
        )
        for item in approvals
        if item.decided_at is not None
    ]
    if not values:
        return _unknown(
            "approvals.decision_latency",
            ProductMetricUnit.DURATION_MS,
            "decision_not_observed",
        )
    return _observed(
        "approvals.decision_latency",
        ProductMetricUnit.DURATION_MS,
        int(median(values)),
        sample_size=len(values),
    )


def _count(
    metric_id: str, value: int, sample_size: int, available: bool
) -> ProductMetricV1:
    if not available:
        return _unknown(metric_id, ProductMetricUnit.COUNT, "source_unavailable")
    return _observed(
        metric_id,
        ProductMetricUnit.COUNT,
        value,
        sample_size=max(sample_size, 1),
    )


def _ratio(
    metric_id: str, numerator: int, denominator: int, available: bool
) -> ProductMetricV1:
    if not available:
        return _unknown(
            metric_id, ProductMetricUnit.RATIO_BASIS_POINTS, "source_unavailable"
        )
    if denominator == 0:
        return _unknown(
            metric_id, ProductMetricUnit.RATIO_BASIS_POINTS, "sample_not_observed"
        )
    return _observed(
        metric_id,
        ProductMetricUnit.RATIO_BASIS_POINTS,
        numerator * 10_000 // denominator,
        sample_size=denominator,
    )


def _observed(
    metric_id: str,
    unit: ProductMetricUnit,
    value: bool | int | str,
    *,
    sample_size: int,
) -> ProductMetricV1:
    return ProductMetricV1(
        metric_id=metric_id,
        status=ProductMetricStatus.OBSERVED,
        unit=unit,
        value=value,
        reason_code="retained_owner_facts",
        sample_size=sample_size,
    )


def _unknown(
    metric_id: str, unit: ProductMetricUnit, reason_code: str
) -> ProductMetricV1:
    return ProductMetricV1(
        metric_id=metric_id,
        status=ProductMetricStatus.UNKNOWN,
        unit=unit,
        value=None,
        reason_code=reason_code,
        sample_size=0,
    )


def _source_count(snapshot: ProductEvidenceSnapshotV1, source: str) -> int:
    return {
        "project": int(snapshot.project_created_at is not None),
        "sessions": len(snapshot.sessions),
        "runs": len(snapshot.runs),
        "jobs": len(snapshot.jobs),
        "attempts": len(snapshot.attempts),
        "approvals": len(snapshot.approvals),
        "review": len(snapshot.policy_events),
        "thread_relay": len(snapshot.deliveries),
        "gateway": len(snapshot.gateway_facts),
        "sidecar": len(snapshot.gateway_facts),
    }[source]


def _report_identity(
    project_id: str,
    range_start: datetime,
    range_end: datetime,
    generated_at: datetime,
    metrics: list[ProductMetricV1],
) -> str:
    payload = {
        "project_id": project_id,
        "range_start": range_start.isoformat(),
        "range_end": range_end.isoformat(),
        "generated_at": generated_at.isoformat(),
        "metrics": [
            [item.metric_id, item.status.value, item.value, item.sample_size]
            for item in sorted(metrics, key=lambda metric: metric.metric_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


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


def _first_timestamp(item: Any, names: tuple[str, ...]) -> datetime | None:
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return _timestamp(value)
    return None


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("product evidence source timestamps must be timezone-aware")
    return parsed


__all__ = [
    "GatewayProductFactV1",
    "ProductEvidenceSnapshotV1",
    "build_product_evidence_report",
    "collect_owner_product_facts",
]
