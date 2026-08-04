from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from gigaloom.contracts.product_evidence import ProductMetricStatus
from gigaloom.review.product_evidence import (
    GatewayProductFactV1,
    ProductEvidenceSnapshotV1,
    build_product_evidence_report,
    collect_owner_product_facts,
)
from gigaloom.runtime.api import ApprovalStatus, JobAttemptStatus, RunStatus
from gigaloom.runtime.policy import (
    EnforcementLevel,
    PermissionAction,
    PolicyAuditEvent,
    PolicyAuditPhase,
    REVIEWED_PROMOTION_APPLY_OWNER,
)


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def _at(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _facts() -> ProductEvidenceSnapshotV1:
    session = SimpleNamespace(id="session-1", created_at=_at(0), updated_at=_at(30))
    runs = (
        SimpleNamespace(
            id="run-1",
            created_at=_at(5),
            updated_at=_at(10),
            finished_at=_at(10),
            status=RunStatus.SUCCEEDED,
        ),
        SimpleNamespace(
            id="run-2",
            created_at=_at(15),
            updated_at=_at(20),
            finished_at=_at(20),
            status=RunStatus.FAILED,
        ),
    )
    jobs = (
        SimpleNamespace(
            id="job-1",
            created_at=_at(5),
            updated_at=_at(10),
            origin="manual",
            workflow_id=None,
            schedule_id=None,
            cancel_requested_at=None,
        ),
        SimpleNamespace(
            id="job-2",
            created_at=_at(15),
            updated_at=_at(20),
            origin="schedule",
            workflow_id=None,
            schedule_id="schedule-1",
            cancel_requested_at=_at(18),
        ),
    )
    attempts = (
        SimpleNamespace(
            status=JobAttemptStatus.SUCCEEDED,
            retry_reason=None,
        ),
        SimpleNamespace(
            status=JobAttemptStatus.INTERRUPTED,
            retry_reason="process_lost",
        ),
    )
    approvals = (
        SimpleNamespace(
            id="approval-1",
            created_at=_at(6),
            decided_at=_at(8),
            status=ApprovalStatus.APPROVED,
        ),
        SimpleNamespace(
            id="approval-2",
            created_at=_at(16),
            decided_at=None,
            status=ApprovalStatus.EXPIRED,
        ),
    )
    accepted = PolicyAuditEvent(
        id="audit-1",
        operation_id="operation-1",
        sequence=3,
        action=PermissionAction.WORKSPACE_WRITE,
        phase=PolicyAuditPhase.ENFORCEMENT,
        decision="allow_once",
        enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        enforcement_owner=REVIEWED_PROMOTION_APPLY_OWNER,
        policy_source="project",
        approval_request_id="approval-1",
        approval_grant_id="grant-1",
        approval_binding_sha256="a" * 64,
        project_id="project-1",
        session_id="session-1",
        run_id="run-1",
        job_id="job-1",
        evidence={},
        previous_event_sha256="b" * 64,
        event_sha256="c" * 64,
        created_at=_at(12),
    )
    deliveries = (
        SimpleNamespace(
            receipt=SimpleNamespace(status=SimpleNamespace(value="completed"))
        ),
        SimpleNamespace(
            receipt=SimpleNamespace(status=SimpleNamespace(value="failed"))
        ),
    )
    return ProductEvidenceSnapshotV1(
        project_created_at=_at(0),
        sessions=(session,),
        runs=runs,
        jobs=jobs,
        attempts=attempts,
        approvals=approvals,
        policy_events=(accepted,),
        deliveries=deliveries,
        gateway_facts=(
            GatewayProductFactV1("ready", "technical_preview", "cold"),
            GatewayProductFactV1("blocked", "blocked", "warm"),
        ),
        available_sources=(
            "sessions",
            "project",
            "runs",
            "jobs",
            "attempts",
            "approvals",
            "review",
            "thread_relay",
            "gateway",
            "sidecar",
        ),
    )


def test_projection_computes_content_free_activation_recovery_and_usage_metrics() -> (
    None
):
    report = build_product_evidence_report(
        project_id="project-1",
        range_start=NOW,
        range_end=NOW + timedelta(hours=1),
        generated_at=NOW + timedelta(hours=1),
        snapshot=_facts(),
    )
    metrics = {item.metric_id: item for item in report.metrics}

    assert metrics["activation.project_to_first_success"].value == 600_000
    assert metrics["activation.project_to_first_accepted_change"].value == 720_000
    assert metrics["runs.first_status"].value == "succeeded"
    assert metrics["runs.success_ratio"].value == 5_000
    assert metrics["approvals.decision_latency"].value == 120_000
    assert metrics["approvals.abandoned"].value == 1
    assert metrics["interventions.cancellations"].value == 1
    assert metrics["recovery.interrupted_or_retried"].value == 1
    assert metrics["review.accepted_changes"].value == 1
    assert metrics["usage.work"].value == 1
    assert metrics["usage.automations"].value == 1
    assert metrics["relay.completed_ratio"].value == 5_000
    assert metrics["relay.rejected"].value == 1
    assert metrics["gateway.preflight_success_ratio"].value == 5_000
    assert metrics["gateway.unsupported_routes"].value == 1
    assert metrics["gateway.sidecar_cold_attach"].value == 1
    assert report.omissions == ()
    assert report.content_free is True
    assert report.local_only is True


def test_projection_keeps_unavailable_or_unobserved_metrics_unknown() -> None:
    report = build_product_evidence_report(
        project_id="project-1",
        range_start=NOW,
        range_end=NOW + timedelta(hours=1),
        generated_at=NOW + timedelta(hours=1),
        snapshot=ProductEvidenceSnapshotV1(
            available_sources=("runs", "sessions"),
        ),
    )
    metrics = {item.metric_id: item for item in report.metrics}

    assert (
        metrics["activation.project_to_first_success"].status
        is ProductMetricStatus.UNKNOWN
    )
    assert (
        metrics["gateway.preflight_success_ratio"].status is ProductMetricStatus.UNKNOWN
    )
    assert report.omissions == ("gateway", "sidecar", "thread_relay")


def test_owner_collection_uses_bounded_metadata_queries_and_project_scope() -> None:
    session = SimpleNamespace(
        id="session-1",
        created_at=_at(1),
        updated_at=_at(10),
    )
    run = SimpleNamespace(
        id="run-1",
        created_at=_at(2),
        updated_at=_at(5),
        finished_at=_at(5),
    )
    job = SimpleNamespace(
        id="job-1",
        project_id="project-1",
        created_at=_at(2),
        updated_at=_at(5),
    )
    attempt = SimpleNamespace(created_at=_at(2), updated_at=_at(5))
    approval = SimpleNamespace(
        id="approval-1",
        project_id="project-1",
        created_at=_at(3),
        decided_at=_at(4),
    )

    class SessionOwner:
        def list_sessions(self, **kwargs: object) -> tuple[object, ...]:
            assert kwargs == {
                "project_id": "project-1",
                "include_archived": True,
                "limit": 257,
            }
            return (session,)

        def list_runs_page(self, session_id: str, **kwargs: object) -> object:
            assert session_id == "session-1"
            assert kwargs == {"cursor": None, "limit": 100}
            return SimpleNamespace(
                items=(run,),
                has_more=False,
                next_cursor=None,
            )

    class RuntimeOwner:
        def list_jobs_page(self, **kwargs: object) -> tuple[tuple[object, ...], bool]:
            assert kwargs == {
                "project_id": "project-1",
                "cursor": None,
                "limit": 100,
            }
            return (job,), False

        def list_attempts(self, job_id: str | None = None) -> tuple[object, ...]:
            assert job_id == "job-1"
            return (attempt,)

        def list_approval_requests(self, **kwargs: object) -> tuple[object, ...]:
            assert kwargs == {"limit": 200}
            return (approval,)

        def list_policy_audit_events(self, **kwargs: object) -> tuple[object, ...]:
            assert kwargs == {"limit": 1_000}
            return ()

    snapshot = collect_owner_product_facts(
        project_id="project-1",
        project_created_at=_at(0),
        range_start=NOW,
        range_end=NOW + timedelta(hours=1),
        session_owner=SessionOwner(),
        runtime_owner=RuntimeOwner(),
    )

    assert snapshot.project_created_at == _at(0)
    assert snapshot.sessions == (session,)
    assert snapshot.runs == (run,)
    assert snapshot.jobs == (job,)
    assert snapshot.attempts == (attempt,)
    assert snapshot.approvals == (approval,)
    assert snapshot.available_sources == (
        "approvals",
        "attempts",
        "jobs",
        "project",
        "review",
        "runs",
        "sessions",
    )
