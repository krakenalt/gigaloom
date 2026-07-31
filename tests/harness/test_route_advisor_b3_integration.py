"""B3 integration tests for manual route authority and exact execution."""

from __future__ import annotations

import argparse
from dataclasses import replace
from decimal import Decimal

import pytest

from gigaloom.cli_commands.commands.route_advisor import add_run_binding_arguments
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
    RouteDecisionVerificationError,
    RouteRunBindingError,
    RouteRunConfirmationV1,
    create_route_decision_receipt,
    execute_confirmed_route,
    override_route_decision_receipt,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
RECEIPT_TIME = "2026-07-31T14:00:00Z"
OVERRIDE_TIME = "2026-07-31T14:01:00Z"
CONFIRMATION_TIME = "2026-07-31T14:02:00Z"


def test_manual_override_requires_new_confirmation_for_exact_route() -> None:
    initial = _receipt()
    original_confirmation = _confirmation(initial)
    overridden = override_route_decision_receipt(
        initial,
        route_id="agent-b.acp",
        reason_code="operator_selected",
        created_at=OVERRIDE_TIME,
    )

    assert initial.recommended_route_id == "agent-a.acp"
    assert overridden.recommended_route_id == "agent-b.acp"
    assert overridden.route_decision_id != initial.route_decision_id
    assert overridden.receipt_digest != initial.receipt_digest
    with pytest.raises(RouteRunBindingError, match="confirmation does not match"):
        execute_confirmed_route(
            overridden,
            confirmation=original_confirmation,
            request={"task_digest": DIGEST_A},
            evidence_source=_StaticEvidenceSource(_current(overridden)),
            runner=_RecordingRunner(),
        )

    runner = _RecordingRunner()
    result = execute_confirmed_route(
        overridden,
        confirmation=_confirmation(overridden),
        request={"task_digest": DIGEST_A},
        evidence_source=_StaticEvidenceSource(_current(overridden)),
        runner=runner,
    )

    assert result == "agent-b.acp"
    assert runner.calls == ["agent-b.acp"]


@pytest.mark.parametrize(
    ("binding_field", "changed_value"),
    [
        ("task_digest", "1" * 64),
        ("context_manifest_digest", "2" * 64),
        ("project_catalog_digest", "3" * 64),
        ("launch_profile_digest", "4" * 64),
        ("capability_catalog_digest", "5" * 64),
        ("cost_policy_digest", "6" * 64),
    ],
)
def test_stale_authority_bindings_block_before_runner_call(
    binding_field: str,
    changed_value: str,
) -> None:
    receipt = _receipt()
    current = _current(receipt)
    source = _StaticEvidenceSource(
        replace(
            current,
            bindings=replace(
                current.bindings,
                **{binding_field: changed_value},
            ),
        )
    )
    runner = _RecordingRunner()

    with pytest.raises(RouteDecisionVerificationError, match=binding_field):
        execute_confirmed_route(
            receipt,
            confirmation=_confirmation(receipt),
            request={"task_digest": DIGEST_A},
            evidence_source=source,
            runner=runner,
        )

    assert runner.calls == []


@pytest.mark.parametrize(
    ("evidence_field", "changed_value"),
    [
        ("route_id", "agent-b.acp"),
        ("agent_id", "agent-c"),
        ("profile_digest", "7" * 64),
        ("capability_snapshot_digest", "8" * 64),
        ("account_digest", "9" * 64),
        ("transport_class", "provider_rpc_v1"),
    ],
)
def test_agent_account_and_profile_switches_require_new_receipt(
    evidence_field: str,
    changed_value: str,
) -> None:
    receipt = _receipt()
    current = replace(_current(receipt), **{evidence_field: changed_value})
    runner = _RecordingRunner()

    with pytest.raises(RouteRunBindingError, match=evidence_field):
        execute_confirmed_route(
            receipt,
            confirmation=_confirmation(receipt),
            request={"task_digest": DIGEST_A},
            evidence_source=_StaticEvidenceSource(current),
            runner=runner,
        )

    assert runner.calls == []


def test_cost_evidence_drift_requires_new_receipt() -> None:
    receipt = _receipt()
    current = _current(receipt)
    current = replace(
        current,
        cost=replace(current.cost, amount=Decimal("2.00")),
    )
    runner = _RecordingRunner()

    with pytest.raises(RouteRunBindingError, match="cost"):
        execute_confirmed_route(
            receipt,
            confirmation=_confirmation(receipt),
            request={"task_digest": DIGEST_A},
            evidence_source=_StaticEvidenceSource(current),
            runner=runner,
        )

    assert runner.calls == []


def test_route_becoming_ineligible_blocks_before_runner_call() -> None:
    receipt = _receipt()
    current = replace(
        _current(receipt),
        eligible=False,
        rejection_codes=("account_identity_drifted",),
    )
    runner = _RecordingRunner()

    with pytest.raises(RouteRunBindingError, match="no longer eligible"):
        execute_confirmed_route(
            receipt,
            confirmation=_confirmation(receipt),
            request={"task_digest": DIGEST_A},
            evidence_source=_StaticEvidenceSource(current),
            runner=runner,
        )

    assert runner.calls == []


def test_generic_runner_failure_propagates_without_hidden_fallback() -> None:
    overridden = override_route_decision_receipt(
        _receipt(),
        route_id="agent-b.acp",
        reason_code="operator_selected",
        created_at=OVERRIDE_TIME,
    )
    runner = _RecordingRunner(fail=True)

    with pytest.raises(RuntimeError, match="structured run failed"):
        execute_confirmed_route(
            overridden,
            confirmation=_confirmation(overridden),
            request={"task_digest": DIGEST_A},
            evidence_source=_StaticEvidenceSource(_current(overridden)),
            runner=runner,
        )

    assert runner.calls == ["agent-b.acp"]
    assert "agent-a.acp" not in runner.calls


def test_shared_run_parser_accepts_explicit_receipt_and_confirmation_refs() -> None:
    parser = argparse.ArgumentParser(prog="giga run")
    add_run_binding_arguments(parser)

    parsed = parser.parse_args(
        [
            "--route-receipt",
            "route_receipt_fixture",
            "--route-confirmation",
            "confirm_fixture",
        ]
    )

    assert parsed.route_receipt == "route_receipt_fixture"
    assert parsed.route_confirmation == "confirm_fixture"


class _StaticEvidenceSource:
    def __init__(self, current: CurrentRouteRunEvidenceV1) -> None:
        self.current = current
        self.requests: list[tuple[str, str]] = []

    def current_evidence(
        self,
        *,
        route_decision_id: str,
        route_id: str,
    ) -> CurrentRouteRunEvidenceV1:
        self.requests.append((route_decision_id, route_id))
        return self.current


class _RecordingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def run(self, plan, request):
        assert request["task_digest"] == DIGEST_A
        assert plan.fallback_allowed is False
        assert plan.execution_attempts == 1
        self.calls.append(plan.route_id)
        if self.fail:
            raise RuntimeError("structured run failed")
        return plan.route_id


def _receipt():
    routes = (
        _eligible_route(
            route_id="agent-a.acp",
            agent_id="agent-a",
            profile_digest=DIGEST_A,
            capability_digest=DIGEST_B,
            account_digest=DIGEST_C,
            rank=1,
        ),
        _eligible_route(
            route_id="agent-b.acp",
            agent_id="agent-b",
            profile_digest="d" * 64,
            capability_digest="e" * 64,
            account_digest="f" * 64,
            rank=2,
        ),
    )
    advice = RouteAdviceV1(
        eligible_routes=routes,
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


def _eligible_route(
    *,
    route_id: str,
    agent_id: str,
    profile_digest: str,
    capability_digest: str,
    account_digest: str,
    rank: int,
) -> EligibleRouteEvidence:
    return EligibleRouteEvidence(
        route_id=route_id,
        agent_id=agent_id,
        profile_digest=profile_digest,
        capability_snapshot_digest=capability_digest,
        account_digest=account_digest,
        transport_class="acp_stdio_v1",
        cost=RouteCostEvidence(
            knowledge=RouteCostKnowledge.ESTIMATED,
            currency="USD",
            amount=Decimal("1.25"),
            headroom=Decimal("8.75"),
        ),
        compatibility_grade=CompatibilityGrade.READY,
        policy_priority=rank,
        explicit_preference_match=False,
        exact_capability_match=True,
        latency=None,
        rank=rank,
    )


def _current(receipt):
    selected = next(
        item
        for item in receipt.eligible_routes
        if item.route_id == receipt.recommended_route_id
    )
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


def _confirmation(receipt) -> RouteRunConfirmationV1:
    route_id = receipt.recommended_route_id
    assert route_id is not None
    return RouteRunConfirmationV1(
        confirmation_id="confirm_fixture",
        operator_id="operator_fixture",
        route_decision_id=receipt.route_decision_id,
        receipt_digest=receipt.receipt_digest,
        route_id=route_id,
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
