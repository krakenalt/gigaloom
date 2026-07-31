"""A4 closure matrix for deterministic route decisions and receipts."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
from typing import cast

import pytest

from gigaloom.execution.route_advisor import (
    CapabilityEvidence,
    CompatibilityGrade,
    LatencyEvidence,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteIntent,
    RouteOverrideError,
    RouteRejectionCode,
    RouteRequirementsV1,
    StructuredRouteCandidateV1,
    advise_routes,
    apply_route_override,
)
from gigaloom.review.route_decisions import (
    RouteDecisionBindingsV1,
    RouteDecisionVerificationError,
    create_route_decision_receipt,
    route_decision_receipt_to_dict,
    verify_route_decision_receipt,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "route_advisor" / "two_routes.json"
NOW = "2026-07-31T12:00:00Z"


def test_two_route_fixture_is_deterministic_content_free_and_has_no_fallback() -> None:
    requirements, candidates, bindings = _load_fixture()

    forward = advise_routes(requirements, candidates)
    reverse = advise_routes(requirements, tuple(reversed(candidates)))

    assert forward == reverse
    assert [item.route_id for item in forward.eligible_routes] == [
        "agent-a.acp",
        "agent-b.acp",
    ]
    assert forward.recommended_route_id == "agent-a.acp"
    assert forward.rejected_routes == ()

    receipt = create_route_decision_receipt(forward, bindings, created_at=NOW)
    payload = route_decision_receipt_to_dict(receipt)
    assert "fallback_route_id" not in payload
    assert "execution" not in payload
    assert payload["recommended_route_id"] == "agent-a.acp"
    assert payload["eligible_routes"][0]["cost"]["amount"] == "1.25"


def test_unknown_cost_and_capability_fail_closed_without_becoming_zero() -> None:
    requirements, candidates, _ = _load_fixture()
    unknown = replace(
        candidates[0],
        capabilities=(CapabilityEvidence("structured_output", RouteFactState.UNKNOWN),),
        cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
    )

    advice = advise_routes(requirements, (unknown, candidates[1]))

    rejection = advice.rejected_routes[0]
    assert rejection.route_id == "agent-b.acp"
    assert rejection.reason_codes == (
        RouteRejectionCode.REQUIRED_CAPABILITY_UNKNOWN,
        RouteRejectionCode.MONETARY_COST_UNKNOWN,
    )
    payload = route_decision_receipt_to_dict(
        create_route_decision_receipt(advice, _load_fixture()[2], created_at=NOW)
    )
    rejected_payload = payload["rejected_routes"][0]
    assert "cost" not in rejected_payload
    assert candidates[0].cost.amount == Decimal("1.00")
    assert unknown.cost.amount is None


def test_manual_override_is_eligible_only_and_changes_receipt_identity() -> None:
    requirements, candidates, bindings = _load_fixture()
    advice = advise_routes(requirements, candidates)
    original = create_route_decision_receipt(advice, bindings, created_at=NOW)
    overridden_advice = apply_route_override(
        advice,
        route_id="agent-b.acp",
        reason_code="operator_selected",
        created_at=NOW,
    )
    overridden = create_route_decision_receipt(
        overridden_advice,
        bindings,
        created_at=NOW,
    )

    assert overridden.recommended_route_id == "agent-b.acp"
    assert overridden.override is not None
    assert overridden.route_decision_id != original.route_decision_id
    assert overridden.receipt_digest != original.receipt_digest

    rejected_candidate = replace(
        candidates[0],
        profile_admission=RouteFactState.REJECTED,
    )
    restricted = advise_routes(requirements, (rejected_candidate, candidates[1]))
    with pytest.raises(RouteOverrideError, match="not eligible"):
        apply_route_override(
            restricted,
            route_id="agent-b.acp",
            reason_code="invalid_selection",
            created_at=NOW,
        )


@pytest.mark.parametrize(
    "field_name",
    [
        "task_digest",
        "context_manifest_digest",
        "project_catalog_digest",
        "launch_profile_digest",
        "capability_catalog_digest",
        "cost_policy_digest",
    ],
)
def test_every_current_binding_must_match_before_later_execution(
    field_name: str,
) -> None:
    requirements, candidates, bindings = _load_fixture()
    receipt = create_route_decision_receipt(
        advise_routes(requirements, candidates),
        bindings,
        created_at=NOW,
    )
    stale = replace(bindings, **{field_name: "9" * 64})

    with pytest.raises(RouteDecisionVerificationError, match=field_name):
        verify_route_decision_receipt(receipt, expected_bindings=stale)


def test_account_drift_requires_a_new_decision_identity() -> None:
    requirements, candidates, bindings = _load_fixture()
    original = create_route_decision_receipt(
        advise_routes(requirements, candidates),
        bindings,
        created_at=NOW,
    )
    drifted_candidates = (
        replace(candidates[0], account_digest="9" * 64),
        candidates[1],
    )
    drifted = create_route_decision_receipt(
        advise_routes(requirements, drifted_candidates),
        bindings,
        created_at=NOW,
    )

    assert drifted.route_decision_id != original.route_decision_id
    assert drifted.receipt_digest != original.receipt_digest


def _load_fixture() -> tuple[
    RouteRequirementsV1,
    tuple[StructuredRouteCandidateV1, ...],
    RouteDecisionBindingsV1,
]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    requirement_payload = payload["requirements"]
    requirements = RouteRequirementsV1(
        intent=RouteIntent(requirement_payload["intent"]),
        required_capabilities=tuple(requirement_payload["required_capabilities"]),
        required_transport_classes=tuple(
            requirement_payload["required_transport_classes"]
        ),
        workspace_policy=requirement_payload["workspace_policy"],
        network_policy=requirement_payload["network_policy"],
        cost_policy_ref=requirement_payload["cost_policy_ref"],
        platform=requirement_payload["platform"],
        context_manifest_digest=requirement_payload["context_manifest_digest"],
        project_id=requirement_payload["project_id"],
        launch_profile_digest=requirement_payload["launch_profile_digest"],
        require_known_cost=requirement_payload["require_known_cost"],
    )
    return (
        requirements,
        tuple(_candidate(item) for item in payload["candidates"]),
        RouteDecisionBindingsV1(**payload["bindings"]),
    )


def _candidate(payload: dict[str, object]) -> StructuredRouteCandidateV1:
    satisfied = RouteFactState.SATISFIED
    cost_payload = cast(dict[str, object], payload["cost"])
    capability_ids = cast(list[object], payload["capabilities"])
    grade = str(payload["compatibility_grade"])
    return StructuredRouteCandidateV1(
        route_id=str(payload["route_id"]),
        agent_id=str(payload["agent_id"]),
        profile_digest=str(payload["profile_digest"]),
        transport_class="acp_stdio_v1",
        capabilities=tuple(
            CapabilityEvidence(str(item), satisfied) for item in capability_ids
        ),
        platform_support=("linux",),
        workspace_policies=("read_only",),
        network_policies=("denied",),
        profile_admission=satisfied,
        route_presence=satisfied,
        executable_readiness=satisfied,
        version_readiness=satisfied,
        capability_snapshot_state=satisfied,
        capability_snapshot_digest=str(payload["capability_snapshot_digest"]),
        account_state=satisfied,
        account_digest=str(payload["account_digest"]),
        policy_state=satisfied,
        budget_state=satisfied,
        project_location_state=satisfied,
        sealed_evaluation_state=RouteFactState.UNKNOWN,
        session_portability_state=RouteFactState.UNKNOWN,
        cost=RouteCostEvidence(
            knowledge=RouteCostKnowledge(str(cost_payload["knowledge"])),
            currency=str(cost_payload["currency"]),
            amount=Decimal(str(cost_payload["amount"])),
            headroom=Decimal(str(cost_payload["headroom"])),
        ),
        compatibility_grade=CompatibilityGrade[grade.upper()],
        policy_priority=int(str(payload["policy_priority"])),
        latency=LatencyEvidence(
            comparison_group="fixture.local",
            p95_milliseconds=int(str(payload["latency_ms"])),
            evidence_digest="7" * 64,
        ),
    )
