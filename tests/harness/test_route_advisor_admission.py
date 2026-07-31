"""Focused fail-closed Route Advisor admission tests."""

from dataclasses import replace
from decimal import Decimal

from gigaloom.execution.route_advisor import (
    CapabilityEvidence,
    CompatibilityGrade,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
    RouteIntent,
    RouteRejectionCode,
    RouteRequirementsV1,
    StructuredRouteCandidateV1,
    admit_route,
    canonicalize_candidates,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_admission_accepts_only_a_route_with_every_required_fact() -> None:
    result = admit_route(_requirements(), _candidate())

    assert result.eligible
    assert result.rejection_codes == ()


def test_admission_rejects_false_and_unknown_facts_with_stable_codes() -> None:
    candidate = replace(
        _candidate(),
        profile_admission=RouteFactState.UNKNOWN,
        version_readiness=RouteFactState.REJECTED,
        capability_snapshot_state=RouteFactState.REJECTED,
        account_state=RouteFactState.UNKNOWN,
        account_digest=None,
        policy_state=RouteFactState.UNKNOWN,
        budget_state=RouteFactState.REJECTED,
        project_location_state=RouteFactState.UNKNOWN,
        capabilities=(CapabilityEvidence("structured_output", RouteFactState.UNKNOWN),),
    )

    result = admit_route(_requirements(), candidate)

    assert not result.eligible
    assert result.rejection_codes == (
        RouteRejectionCode.PROFILE_ADMISSION_UNKNOWN,
        RouteRejectionCode.VERSION_NOT_READY,
        RouteRejectionCode.CAPABILITY_SNAPSHOT_STALE,
        RouteRejectionCode.REQUIRED_CAPABILITY_UNKNOWN,
        RouteRejectionCode.ACCOUNT_IDENTITY_UNRESOLVED,
        RouteRejectionCode.POLICY_UNKNOWN,
        RouteRejectionCode.BUDGET_NOT_ADMITTED,
        RouteRejectionCode.PROJECT_LOCATION_UNKNOWN,
    )


def test_unknown_cost_is_not_zero_and_fails_only_when_policy_requires_known() -> None:
    unknown_cost = RouteCostEvidence(RouteCostKnowledge.UNKNOWN)
    candidate = replace(_candidate(), cost=unknown_cost)

    permissive = admit_route(_requirements(require_known_cost=False), candidate)
    strict = admit_route(_requirements(require_known_cost=True), candidate)

    assert permissive.eligible
    assert strict.rejection_codes == (RouteRejectionCode.MONETARY_COST_UNKNOWN,)
    assert candidate.cost.amount is None
    assert candidate.cost.currency is None


def test_candidate_order_is_stable_and_duplicate_route_ids_fail() -> None:
    route_b = replace(_candidate(), route_id="route.b")
    route_a = replace(_candidate(), route_id="route.a")

    assert [item.route_id for item in canonicalize_candidates((route_b, route_a))] == [
        "route.a",
        "route.b",
    ]

    try:
        canonicalize_candidates((route_a, route_a))
    except ValueError as exc:
        assert str(exc) == "route candidate ids must be unique"
    else:
        raise AssertionError("duplicate route ids must fail closed")


def _requirements(*, require_known_cost: bool = True) -> RouteRequirementsV1:
    return RouteRequirementsV1(
        intent=RouteIntent.READ,
        required_capabilities=("structured_output",),
        required_transport_classes=("acp_stdio_v1",),
        workspace_policy="read_only",
        network_policy="denied",
        cost_policy_ref="budget.read",
        platform="linux",
        context_manifest_digest=DIGEST_A,
        project_id="prj_test",
        required_host_id="local",
        required_account_digest=DIGEST_B,
        require_known_cost=require_known_cost,
    )


def _candidate() -> StructuredRouteCandidateV1:
    satisfied = RouteFactState.SATISFIED
    return StructuredRouteCandidateV1(
        route_id="test.acp",
        agent_id="test-agent",
        profile_digest=DIGEST_A,
        transport_class="acp_stdio_v1",
        capabilities=(CapabilityEvidence("structured_output", satisfied),),
        platform_support=("linux", "macos"),
        workspace_policies=("read_only",),
        network_policies=("denied",),
        profile_admission=satisfied,
        route_presence=satisfied,
        executable_readiness=satisfied,
        version_readiness=satisfied,
        capability_snapshot_state=satisfied,
        capability_snapshot_digest=DIGEST_B,
        account_state=satisfied,
        account_digest=DIGEST_B,
        policy_state=satisfied,
        budget_state=satisfied,
        project_location_state=satisfied,
        sealed_evaluation_state=RouteFactState.UNKNOWN,
        session_portability_state=RouteFactState.UNKNOWN,
        cost=RouteCostEvidence(
            RouteCostKnowledge.ESTIMATED,
            currency="USD",
            amount=Decimal("1.25"),
            headroom=Decimal("8.75"),
        ),
        compatibility_grade=CompatibilityGrade.READY,
        policy_priority=10,
        host_id="local",
    )
