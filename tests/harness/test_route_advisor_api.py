"""B3 HTTP parity tests for Route Advisor inspection and override."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.execution.api import (
    CapabilityEvidence,
    CompatibilityGrade,
    RouteAdvisorApplicationService,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteRecommendationQueryV1,
    RouteRecommendationSnapshotV1,
    RouteRequirementsV1,
    StructuredRouteCandidateV1,
)
from gigaloom.review.api import RouteDecisionRepository
from gigaloom.ui.routers.route_advisor import create_router
from gigaloom.ui.services.route_advisor import RouteAdvisorWebService


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
NOW = datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc)


class _Source:
    calls = 0

    def snapshot(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteRecommendationSnapshotV1:
        self.calls += 1
        return RouteRecommendationSnapshotV1(
            requirements=RouteRequirementsV1(
                intent=query.intent,
                required_capabilities=query.required_capabilities,
                required_transport_classes=query.required_transport_classes,
                workspace_policy=query.workspace_policy,
                network_policy=query.network_policy,
                cost_policy_ref=query.cost_policy_ref,
                platform=query.platform,
                context_manifest_digest=query.context_manifest_digest,
                project_id=query.project_id,
                launch_profile_digest=None,
                preferred_route_id=query.preferred_route_id,
                required_host_id=query.required_host_id,
                required_account_digest=query.required_account_digest,
                require_known_cost=query.require_known_cost,
                require_sealed_evaluation=query.require_sealed_evaluation,
                require_session_portability=query.require_session_portability,
            ),
            candidates=(
                _candidate("agent-a.acp", priority=0),
                _candidate("agent-b.acp", priority=1),
                _candidate("agent-c.acp", priority=2, admitted=False),
            ),
            project_catalog_digest=DIGEST_A,
            capability_catalog_digest=DIGEST_B,
            cost_policy_digest=DIGEST_C,
        )


def test_web_inspects_eligible_rejected_selected_and_manual_override(tmp_path) -> None:
    source = _Source()
    service = RouteAdvisorWebService(
        advisor=RouteAdvisorApplicationService(source),
        repository=RouteDecisionRepository(tmp_path / "route-decisions"),
        clock=lambda: NOW,
    )
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app)

    recommendation = client.post(
        "/api/route-decisions/recommend",
        json={
            "project_id": "prj_fixture",
            "intent": "read",
            "task_digest": DIGEST_A,
            "context_manifest_digest": DIGEST_B,
            "required_capabilities": ["structured_prompt"],
            "required_transport_classes": ["acp_stdio_v1"],
            "workspace_policy": "read_only",
            "network_policy": "denied",
            "cost_policy_ref": "explicit_unlimited",
            "platform": "linux",
        },
    )

    assert recommendation.status_code == 200
    payload = recommendation.json()
    receipt = payload["receipt"]
    assert payload["execution_started"] is False
    assert payload["confirmation_required"] is True
    assert receipt["recommended_route_id"] == "agent-a.acp"
    assert [item["route_id"] for item in receipt["eligible_routes"]] == [
        "agent-a.acp",
        "agent-b.acp",
    ]
    assert receipt["eligible_routes"][0]["cost"] == {
        "knowledge": "unknown",
        "currency": None,
        "amount": None,
        "headroom": None,
    }
    assert receipt["rejected_routes"] == [
        {
            "route_id": "agent-c.acp",
            "agent_id": "agent-c",
            "reason_codes": ["profile_not_admitted"],
        }
    ]

    receipt_id = receipt["route_decision_id"]
    inspected = client.get(f"/api/route-decisions/{receipt_id}")
    assert inspected.status_code == 200
    assert inspected.json() == payload

    overridden = client.post(
        f"/api/route-decisions/{receipt_id}/override",
        json={"route_id": "agent-b.acp", "reason_code": "operator_selected"},
    )
    assert overridden.status_code == 200
    override_receipt = overridden.json()["receipt"]
    assert override_receipt["route_decision_id"] != receipt_id
    assert override_receipt["recommended_route_id"] == "agent-b.acp"
    assert override_receipt["override"]["reason_code"] == "operator_selected"
    assert source.calls == 1

    rejected_override = client.post(
        f"/api/route-decisions/{receipt_id}/override",
        json={"route_id": "agent-c.acp", "reason_code": "operator_selected"},
    )
    assert rejected_override.status_code == 409


def _candidate(
    route_id: str,
    *,
    priority: int,
    admitted: bool = True,
) -> StructuredRouteCandidateV1:
    satisfied = RouteFactState.SATISFIED
    return StructuredRouteCandidateV1(
        route_id=route_id,
        agent_id=route_id.split(".", maxsplit=1)[0],
        profile_digest=str(priority + 1) * 64,
        transport_class="acp_stdio_v1",
        capabilities=(CapabilityEvidence("structured_prompt", satisfied),),
        platform_support=("linux",),
        workspace_policies=("read_only",),
        network_policies=("denied",),
        profile_admission=(satisfied if admitted else RouteFactState.REJECTED),
        route_presence=satisfied,
        executable_readiness=satisfied,
        version_readiness=satisfied,
        capability_snapshot_state=satisfied,
        capability_snapshot_digest=str(priority + 4) * 64,
        account_state=satisfied,
        account_digest=str(priority + 7) * 64,
        policy_state=satisfied,
        budget_state=satisfied,
        project_location_state=satisfied,
        sealed_evaluation_state=RouteFactState.UNKNOWN,
        session_portability_state=RouteFactState.UNKNOWN,
        cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
        compatibility_grade=CompatibilityGrade.READY,
        policy_priority=priority,
    )
