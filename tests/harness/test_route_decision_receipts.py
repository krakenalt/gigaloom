"""Focused immutable route decision receipt tests."""

from dataclasses import replace
from decimal import Decimal

import pytest

from gigaloom.execution.route_advisor import (
    CompatibilityGrade,
    EligibleRouteEvidence,
    RouteAdviceOutcome,
    RouteAdviceV1,
    RouteCostEvidence,
    RouteCostKnowledge,
)
from gigaloom.review.route_decisions import (
    RouteDecisionBindingsV1,
    RouteDecisionConflictError,
    RouteDecisionRepository,
    RouteDecisionVerificationError,
    canonical_route_decision_bytes,
    create_route_decision_receipt,
    route_decision_receipt_from_dict,
    route_decision_receipt_to_dict,
    verify_route_decision_receipt,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
NOW = "2026-07-31T10:00:00Z"


def test_receipt_is_deterministic_content_free_and_round_trips() -> None:
    receipt = create_route_decision_receipt(_advice(), _bindings(), created_at=NOW)
    duplicate = create_route_decision_receipt(_advice(), _bindings(), created_at=NOW)

    assert receipt == duplicate
    assert receipt.route_decision_id.startswith("route_")
    assert (
        route_decision_receipt_from_dict(route_decision_receipt_to_dict(receipt))
        == receipt
    )
    encoded = canonical_route_decision_bytes(receipt)
    assert b"raw prompt" not in encoded
    assert encoded.endswith(b"\n")


def test_repository_is_idempotent_and_never_overwrites_a_decision(
    tmp_path,
) -> None:
    repository = RouteDecisionRepository(tmp_path / "route-decisions")
    receipt = create_route_decision_receipt(
        _advice(),
        _bindings(),
        created_at=NOW,
        route_decision_id="route_fixed",
    )
    conflict = create_route_decision_receipt(
        _advice(),
        replace(_bindings(), task_digest=DIGEST_C),
        created_at=NOW,
        route_decision_id="route_fixed",
    )

    assert repository.save(receipt) == receipt
    assert repository.save(receipt) == receipt
    assert repository.get(receipt.route_decision_id) == receipt
    with pytest.raises(RouteDecisionConflictError, match="different immutable"):
        repository.save(conflict)


def test_verification_rejects_tamper_and_stale_bindings() -> None:
    receipt = create_route_decision_receipt(_advice(), _bindings(), created_at=NOW)
    payload = route_decision_receipt_to_dict(receipt)
    payload["task_digest"] = DIGEST_C

    with pytest.raises(ValueError, match="digest mismatch"):
        route_decision_receipt_from_dict(payload)
    with pytest.raises(RouteDecisionVerificationError, match="project_catalog_digest"):
        verify_route_decision_receipt(
            receipt,
            expected_bindings=replace(
                _bindings(),
                project_catalog_digest=DIGEST_C,
            ),
        )


def _bindings() -> RouteDecisionBindingsV1:
    return RouteDecisionBindingsV1(
        task_digest=DIGEST_A,
        context_manifest_digest=DIGEST_B,
        project_catalog_digest=DIGEST_A,
        launch_profile_digest=None,
        capability_catalog_digest=DIGEST_B,
        cost_policy_digest=DIGEST_A,
    )


def _advice() -> RouteAdviceV1:
    eligible = EligibleRouteEvidence(
        route_id="route.a",
        agent_id="agent-a",
        profile_digest=DIGEST_A,
        capability_snapshot_digest=DIGEST_B,
        account_digest=DIGEST_C,
        transport_class="acp_stdio_v1",
        cost=RouteCostEvidence(
            knowledge=RouteCostKnowledge.ESTIMATED,
            currency="USD",
            amount=Decimal("1.25"),
            headroom=Decimal("8.75"),
        ),
        compatibility_grade=CompatibilityGrade.READY,
        policy_priority=10,
        explicit_preference_match=False,
        exact_capability_match=True,
        latency=None,
        rank=1,
    )
    return RouteAdviceV1(
        eligible_routes=(eligible,),
        rejected_routes=(),
        recommended_route_id="route.a",
        ranker_id="policy_ranker_v1",
        ranker_version="1",
        outcome=RouteAdviceOutcome.RECOMMENDED,
    )
