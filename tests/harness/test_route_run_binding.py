"""Focused unit tests for exact pre-run route receipt revalidation."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from gigaloom.execution.api import (
    CompatibilityGrade,
    RouteAdviceOutcome,
    RouteAdviceV1,
    RouteCostEvidence,
    RouteCostKnowledge,
)
from gigaloom.execution.route_advisor import EligibleRouteEvidence
from gigaloom.review.api import (
    CurrentRouteRunEvidenceV1,
    RouteDecisionBindingsV1,
    RouteRunBindingError,
    RouteRunConfirmationV1,
    create_route_decision_receipt,
    execute_confirmed_route,
    revalidate_route_decision_for_run,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
RECEIPT_TIME = "2026-07-31T13:00:00Z"
CONFIRMATION_TIME = "2026-07-31T13:01:00Z"


def test_revalidation_builds_one_exact_non_fallback_plan() -> None:
    receipt = _receipt()
    current = _current(receipt)
    plan = revalidate_route_decision_for_run(
        receipt,
        current=current,
        confirmation=_confirmation(receipt),
    )

    assert plan.route_id == "agent-a.acp"
    assert plan.agent_id == "agent-a"
    assert plan.profile_digest == DIGEST_A
    assert plan.capability_snapshot_digest == DIGEST_B
    assert plan.account_digest == DIGEST_C
    assert plan.fallback_allowed is False
    assert plan.execution_attempts == 1


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("profile_digest", "9" * 64),
        ("capability_snapshot_digest", "8" * 64),
        ("account_digest", "7" * 64),
        ("transport_class", "provider_rpc_v1"),
    ],
)
def test_revalidation_rejects_selected_route_evidence_drift(
    field_name: str,
    value: str,
) -> None:
    receipt = _receipt()
    current = replace(_current(receipt), **{field_name: value})

    with pytest.raises(RouteRunBindingError, match=field_name):
        revalidate_route_decision_for_run(
            receipt,
            current=current,
            confirmation=_confirmation(receipt),
        )


def test_execute_loads_current_evidence_before_one_exact_runner_call() -> None:
    receipt = _receipt()

    class Source:
        calls: list[tuple[str, str]] = []

        def current_evidence(self, *, route_decision_id: str, route_id: str):
            self.calls.append((route_decision_id, route_id))
            return _current(receipt)

    class Runner:
        calls = []

        def run(self, plan, request):
            self.calls.append((plan, request))
            return {"route_id": plan.route_id, "request": request}

    source = Source()
    runner = Runner()
    result = execute_confirmed_route(
        receipt,
        confirmation=_confirmation(receipt),
        request={"content_digest": "4" * 64},
        evidence_source=source,
        runner=runner,
    )

    assert result == {
        "route_id": "agent-a.acp",
        "request": {"content_digest": "4" * 64},
    }
    assert source.calls == [(receipt.route_decision_id, "agent-a.acp")]
    assert len(runner.calls) == 1


def test_confirmation_must_match_exact_receipt_and_selected_route() -> None:
    receipt = _receipt()
    mismatched = replace(_confirmation(receipt), receipt_digest="9" * 64)

    with pytest.raises(RouteRunBindingError, match="receipt_digest"):
        revalidate_route_decision_for_run(
            receipt,
            current=_current(receipt),
            confirmation=mismatched,
        )


def _receipt():
    eligible = EligibleRouteEvidence(
        route_id="agent-a.acp",
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
        policy_priority=0,
        explicit_preference_match=False,
        exact_capability_match=True,
        latency=None,
        rank=1,
    )
    advice = RouteAdviceV1(
        eligible_routes=(eligible,),
        rejected_routes=(),
        recommended_route_id="agent-a.acp",
        ranker_id="policy_ranker_v1",
        ranker_version="1",
        outcome=RouteAdviceOutcome.RECOMMENDED,
    )
    return create_route_decision_receipt(
        advice,
        _bindings(),
        created_at=RECEIPT_TIME,
    )


def _current(receipt):
    selected = receipt.eligible_routes[0]
    return CurrentRouteRunEvidenceV1(
        bindings=receipt.bindings,
        route_id=selected.route_id,
        agent_id=selected.agent_id,
        profile_digest=selected.profile_digest,
        capability_snapshot_digest=selected.capability_snapshot_digest,
        account_digest=selected.account_digest,
        transport_class=selected.transport_class,
        cost=selected.cost,
        eligible=True,
    )


def _confirmation(receipt):
    return RouteRunConfirmationV1(
        confirmation_id="confirm_fixture",
        operator_id="operator_fixture",
        route_decision_id=receipt.route_decision_id,
        receipt_digest=receipt.receipt_digest,
        route_id="agent-a.acp",
        confirmed_at=CONFIRMATION_TIME,
    )


def _bindings() -> RouteDecisionBindingsV1:
    return RouteDecisionBindingsV1(
        task_digest=DIGEST_A,
        context_manifest_digest=DIGEST_B,
        project_catalog_digest=DIGEST_C,
        launch_profile_digest=None,
        capability_catalog_digest=DIGEST_A,
        cost_policy_digest=DIGEST_B,
    )
