"""Focused PolicyRankerV1 and manual override tests."""

from dataclasses import replace
from decimal import Decimal

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
    RouteRankingError,
    RouteRequirementsV1,
    StructuredRouteCandidateV1,
    advise_routes,
    apply_route_override,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
NOW = "2026-07-31T10:00:00Z"


def test_policy_ranker_uses_frozen_precedence_and_stable_route_tie_break() -> None:
    exact = _candidate(route_id="route.z", capabilities=("structured_output",))
    extra = _candidate(
        route_id="route.a",
        capabilities=("session_load", "structured_output"),
        grade=CompatibilityGrade.VERIFIED,
        priority=0,
        headroom="99",
        latency_ms=1,
    )

    advice = advise_routes(_requirements(), (extra, exact))

    assert [item.route_id for item in advice.eligible_routes] == [
        "route.z",
        "route.a",
    ]
    assert [item.rank for item in advice.eligible_routes] == [1, 2]
    assert advice.recommended_route_id == "route.z"

    tied = advise_routes(
        _requirements(),
        (
            replace(exact, route_id="route.b"),
            replace(exact, route_id="route.a"),
        ),
    )
    assert [item.route_id for item in tied.eligible_routes] == [
        "route.a",
        "route.b",
    ]


def test_explicit_preference_wins_and_manual_override_must_remain_eligible() -> None:
    route_a = _candidate(route_id="route.a")
    route_b = _candidate(route_id="route.b")
    advice = advise_routes(
        replace(_requirements(), preferred_route_id="route.b"),
        (route_a, route_b),
    )

    assert advice.recommended_route_id == "route.b"

    overridden = apply_route_override(
        advice,
        route_id="route.a",
        reason_code="operator_selected",
        created_at=NOW,
    )
    assert overridden.recommended_route_id == "route.a"
    assert overridden.override is not None
    assert overridden.override.reason_code == "operator_selected"

    rejected = replace(route_a, profile_admission=RouteFactState.REJECTED)
    mixed = advise_routes(_requirements(), (rejected, route_b))
    with pytest.raises(RouteOverrideError, match="not eligible"):
        apply_route_override(
            mixed,
            route_id="route.a",
            reason_code="unsafe_selection",
            created_at=NOW,
        )


def test_ranker_failure_selects_nothing_and_never_falls_back() -> None:
    class BrokenRanker:
        ranker_id = "broken_ranker"
        ranker_version = "1"

        def rank(self, requirements, evaluations):
            del requirements, evaluations
            raise RouteRankingError("fixture failure")

    advice = advise_routes(
        _requirements(),
        (_candidate(route_id="route.a"), _candidate(route_id="route.b")),
        ranker=BrokenRanker(),
    )

    assert advice.recommended_route_id is None
    assert advice.outcome.value == "needs_human"
    assert {item.route_id for item in advice.eligible_routes} == {
        "route.a",
        "route.b",
    }


def _requirements() -> RouteRequirementsV1:
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
        require_known_cost=True,
    )


def _candidate(
    *,
    route_id: str,
    capabilities: tuple[str, ...] = ("structured_output",),
    grade: CompatibilityGrade = CompatibilityGrade.READY,
    priority: int = 10,
    headroom: str = "8.75",
    latency_ms: int = 50,
) -> StructuredRouteCandidateV1:
    satisfied = RouteFactState.SATISFIED
    return StructuredRouteCandidateV1(
        route_id=route_id,
        agent_id=f"agent-{route_id[-1]}",
        profile_digest=DIGEST_A,
        transport_class="acp_stdio_v1",
        capabilities=tuple(
            CapabilityEvidence(item, satisfied) for item in sorted(capabilities)
        ),
        platform_support=("linux",),
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
            headroom=Decimal(headroom),
        ),
        compatibility_grade=grade,
        policy_priority=priority,
        latency=LatencyEvidence(
            comparison_group="fixture.local",
            p95_milliseconds=latency_ms,
            evidence_digest=DIGEST_A,
        ),
    )
