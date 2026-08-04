from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from types import SimpleNamespace

from gigaloom.application.product_evidence import ProductEvidenceApplication
from gigaloom.contracts.product_evidence import ProductMetricStatus


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


class _Catalog:
    def get(self, project_id: str) -> object:
        assert project_id == "project-1"
        return SimpleNamespace(created_at=(NOW - timedelta(days=10)).isoformat())


class _Sessions:
    def list_sessions(self, **kwargs: object) -> tuple[object, ...]:
        assert kwargs["project_id"] == "project-1"
        return ()

    def list_runs_page(self, session_id: str, **kwargs: object) -> object:
        raise AssertionError(f"unexpected session read: {session_id}, {kwargs}")


class _Runtime:
    def list_jobs_page(self, **kwargs: object) -> tuple[tuple[object, ...], bool]:
        assert kwargs["project_id"] == "project-1"
        return (), False

    def list_attempts(self, job_id: str | None = None) -> tuple[object, ...]:
        raise AssertionError(f"unexpected attempt read: {job_id}")

    def list_approval_requests(self, **kwargs: object) -> tuple[object, ...]:
        return ()

    def list_policy_audit_events(self, **kwargs: object) -> tuple[object, ...]:
        return ()


class _RelaySessions(_Sessions):
    def list_sessions(self, **kwargs: object) -> tuple[object, ...]:
        assert kwargs["project_id"] == "project-1"
        return (
            SimpleNamespace(
                id="session-1",
                created_at=(NOW - timedelta(days=2)).isoformat(),
                updated_at=(NOW - timedelta(days=1)).isoformat(),
                workspace="/private/project-1",
                metadata={},
            ),
        )

    def list_runs_page(self, session_id: str, **kwargs: object) -> object:
        assert session_id == "session-1"
        return SimpleNamespace(items=(), has_more=False, next_cursor=None)


class _Deliveries:
    def list_for_thread(self, locator: object, **kwargs: object) -> object:
        assert locator.workspace_identity == (
            "sha256:" + hashlib.sha256(b"/private/project-1").hexdigest()
        )
        assert kwargs["direction"] in {"incoming", "outgoing"}
        return SimpleNamespace(items=(), has_more=False)


def test_application_uses_catalog_creation_and_keeps_missing_owners_unknown() -> None:
    application = ProductEvidenceApplication(
        project_catalog=_Catalog(),
        session_owner=_Sessions(),
        runtime_owner=_Runtime(),
    )

    report = application.report(
        project_id="project-1",
        range_start=NOW - timedelta(days=30),
        range_end=NOW,
        generated_at=NOW,
    )
    metrics = {item.metric_id: item for item in report.metrics}

    assert {source.source_id for source in report.sources} >= {
        "project",
        "sessions",
        "runs",
        "jobs",
    }
    assert (
        metrics["activation.project_to_first_success"].status
        is ProductMetricStatus.UNKNOWN
    )
    assert metrics["relay.deliveries"].status is ProductMetricStatus.UNKNOWN
    assert (
        metrics["gateway.preflight_success_ratio"].status is ProductMetricStatus.UNKNOWN
    )
    assert report.omissions == ("gateway", "sidecar", "thread_relay")


def test_application_uses_relay_workspace_identity_without_leaking_path() -> None:
    application = ProductEvidenceApplication(
        project_catalog=_Catalog(),
        session_owner=_RelaySessions(),
        runtime_owner=_Runtime(),
        delivery_owner=_Deliveries(),
    )

    report = application.report(
        project_id="project-1",
        range_start=NOW - timedelta(days=30),
        range_end=NOW,
        generated_at=NOW,
    )
    metrics = {item.metric_id: item for item in report.metrics}

    assert metrics["relay.deliveries"].value == 0
    assert "thread_relay" not in report.omissions
    assert "/private/project-1" not in str(report)
