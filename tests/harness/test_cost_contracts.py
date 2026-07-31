"""Tests for honest cost, quota, budget, and receipt contracts."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json

import pytest

from gigaloom import contracts
from gigaloom.contracts import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetLease,
    BudgetPolicy,
    BudgetPolicyKind,
    CostConfidence,
    CostObservation,
    CostReceipt,
    CostReceiptOutcome,
    SubscriptionQuotaObservation,
    TokenObservation,
    budget_admission_from_dict,
    budget_admission_to_dict,
    budget_lease_from_dict,
    budget_lease_to_dict,
    budget_policy_from_dict,
    budget_policy_to_dict,
    cost_observation_from_dict,
    cost_observation_to_dict,
    cost_receipt_from_dict,
    cost_receipt_to_dict,
    subscription_quota_observation_from_dict,
    subscription_quota_observation_to_dict,
    token_observation_from_dict,
    token_observation_to_dict,
)


NOW = "2026-07-30T10:00:00Z"
LATER = "2026-07-30T10:05:00Z"
SOURCE_DIGEST = "a" * 64
PRICE_DIGEST = "b" * 64


@pytest.mark.parametrize(
    ("observation", "expected_amount"),
    [
        (
            CostObservation(
                provider_id="openai",
                model_id="gpt-5.6",
                route_class="priced_api",
                confidence=CostConfidence.EXACT,
                source="provider_usage",
                source_digest=SOURCE_DIGEST,
                observed_at=NOW,
                currency="USD",
                amount=Decimal("0"),
            ),
            "0",
        ),
        (
            CostObservation(
                provider_id="openai",
                model_id="gpt-5.6",
                route_class="priced_api",
                confidence=CostConfidence.ESTIMATED,
                source="price_catalog",
                source_digest=SOURCE_DIGEST,
                observed_at=NOW,
                currency="USD",
                amount=Decimal("1.2300"),
                reason_code="catalog_estimate",
            ),
            "1.23",
        ),
        (
            CostObservation(
                provider_id="codex",
                model_id="subscription",
                route_class="native_subscription",
                confidence=CostConfidence.UNKNOWN,
                source="native_session",
                source_digest=SOURCE_DIGEST,
                observed_at=NOW,
                reason_code="monetary_cost_unavailable",
            ),
            None,
        ),
    ],
)
def test_cost_confidence_round_trips_without_turning_unknown_into_zero(
    observation: CostObservation,
    expected_amount: str | None,
) -> None:
    payload = cost_observation_to_dict(observation)

    assert cost_observation_from_dict(payload) == observation
    assert payload["amount"] == expected_amount
    assert payload["currency"] == observation.currency
    assert json.loads(json.dumps(payload))["amount"] == expected_amount


def test_token_and_subscription_quota_stay_separate_from_money() -> None:
    tokens = TokenObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class="priced_api",
        confidence=CostConfidence.EXACT,
        source="provider_usage",
        source_digest=SOURCE_DIGEST,
        observed_at=NOW,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )
    quota = SubscriptionQuotaObservation(
        provider_id="codex",
        route_class="native_subscription",
        quota_name="rolling_window",
        unit="requests",
        confidence=CostConfidence.ESTIMATED,
        source="provider_status",
        source_digest=SOURCE_DIGEST,
        observed_at=NOW,
        used=Decimal("7"),
        limit=Decimal("100"),
        remaining=Decimal("93"),
        reason_code="provider_projection",
    )

    token_payload = token_observation_to_dict(tokens)
    quota_payload = subscription_quota_observation_to_dict(quota)

    assert token_observation_from_dict(token_payload) == tokens
    assert subscription_quota_observation_from_dict(quota_payload) == quota
    assert "currency" not in token_payload
    assert "amount" not in token_payload
    assert "currency" not in quota_payload
    assert "amount" not in quota_payload


def test_budget_admission_lease_and_terminal_receipt_round_trip() -> None:
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    requested = _known_cost(amount="2.50")
    admission = BudgetAdmission(
        id="admission-1",
        policy=policy,
        decision=BudgetAdmissionDecision.ADMITTED,
        route_class=requested.route_class,
        requested_cost=requested,
        reason_code="within_headroom",
        created_at=NOW,
        price_source_digest=PRICE_DIGEST,
    )
    lease = BudgetLease(
        id="lease-child-1",
        admission_id=admission.id,
        parent_lease_id="lease-parent",
        limit=BudgetPolicy.finite("USD", Decimal("2.50")),
        issued_at=NOW,
        expires_at=LATER,
    )
    receipt = CostReceipt(
        id="receipt-1",
        lease_id=lease.id,
        outcome=CostReceiptOutcome.CANCELED,
        final_cost=_unknown_cost(reason_code="canceled_before_final_usage"),
        closed_at=LATER,
        reason_code="child_canceled",
    )

    assert budget_policy_from_dict(budget_policy_to_dict(policy)) == policy
    assert budget_admission_from_dict(budget_admission_to_dict(admission)) == admission
    assert budget_lease_from_dict(budget_lease_to_dict(lease)) == lease
    assert cost_receipt_from_dict(cost_receipt_to_dict(receipt)) == receipt
    assert receipt.final_cost.confidence is CostConfidence.UNKNOWN
    assert cost_receipt_to_dict(receipt)["final_cost"]["amount"] is None


def test_unlimited_policy_is_explicit_and_does_not_invent_a_cost() -> None:
    policy = BudgetPolicy.unlimited()
    unknown = _unknown_cost()
    admission = BudgetAdmission(
        id="admission-unlimited",
        policy=policy,
        decision=BudgetAdmissionDecision.ADMITTED,
        route_class=unknown.route_class,
        requested_cost=unknown,
        reason_code="explicit_unlimited_policy",
        created_at=NOW,
    )

    assert policy.kind is BudgetPolicyKind.UNLIMITED
    assert budget_policy_to_dict(policy) == {
        "schema_version": 1,
        "kind": "unlimited",
        "currency": None,
        "amount": None,
    }
    assert budget_admission_from_dict(budget_admission_to_dict(admission)) == admission


def test_finite_policy_rejects_unknown_cost_and_mismatched_price_evidence() -> None:
    policy = BudgetPolicy.finite("USD", Decimal("5"))

    with pytest.raises(ValueError, match="cannot admit unknown"):
        BudgetAdmission(
            id="admission-unknown",
            policy=policy,
            decision=BudgetAdmissionDecision.ADMITTED,
            route_class="native_subscription",
            requested_cost=_unknown_cost(),
            reason_code="incorrect_admission",
            created_at=NOW,
            price_source_digest=PRICE_DIGEST,
        )

    with pytest.raises(ValueError, match="price source digest"):
        BudgetAdmission(
            id="admission-no-source",
            policy=policy,
            decision=BudgetAdmissionDecision.ADMITTED,
            route_class="priced_api",
            requested_cost=_known_cost(),
            reason_code="incorrect_admission",
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="exceeds finite policy"):
        BudgetAdmission(
            id="admission-over-budget",
            policy=policy,
            decision=BudgetAdmissionDecision.ADMITTED,
            route_class="priced_api",
            requested_cost=_known_cost(amount="5.01"),
            reason_code="incorrect_admission",
            created_at=NOW,
            price_source_digest=PRICE_DIGEST,
        )


def test_unknown_observations_cannot_smuggle_values_or_omit_provenance() -> None:
    with pytest.raises(ValueError, match="unknown cost observation"):
        replace(_unknown_cost(), currency="USD", amount=Decimal("0"))

    with pytest.raises(ValueError, match="requires a reason_code"):
        replace(_unknown_cost(), reason_code=None)

    with pytest.raises(ValueError, match="source_digest"):
        replace(_unknown_cost(), source_digest="not-a-digest")

    with pytest.raises(ValueError, match="timezone"):
        replace(_unknown_cost(), observed_at="2026-07-30T10:00:00")


def test_wire_parsers_reject_future_unknown_and_ambiguous_numeric_fields() -> None:
    payload = cost_observation_to_dict(_known_cost())

    with pytest.raises(ValueError, match="schema_version"):
        cost_observation_from_dict({**payload, "schema_version": 2})

    with pytest.raises(ValueError, match="unknown cost observation fields"):
        cost_observation_from_dict({**payload, "api_key": "must-not-be-accepted"})

    with pytest.raises(ValueError, match="decimal string"):
        cost_observation_from_dict({**payload, "amount": 1.25})

    with pytest.raises(ValueError, match="cost confidence"):
        cost_observation_from_dict({**payload, "confidence": "free"})


def test_cost_contracts_are_exported_only_from_the_canonical_contract_package() -> None:
    assert contracts.CostObservation is CostObservation
    assert CostObservation.__module__ == "gigaloom.contracts.cost"
    assert budget_policy_to_dict(BudgetPolicy.unlimited())["kind"] == "unlimited"


def _known_cost(*, amount: str = "1.25") -> CostObservation:
    return CostObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class="priced_api",
        confidence=CostConfidence.ESTIMATED,
        source="price_catalog",
        source_digest=SOURCE_DIGEST,
        observed_at=NOW,
        currency="USD",
        amount=Decimal(amount),
        reason_code="catalog_estimate",
    )


def _unknown_cost(
    *,
    reason_code: str = "monetary_cost_unavailable",
) -> CostObservation:
    return CostObservation(
        provider_id="codex",
        model_id="subscription",
        route_class="native_subscription",
        confidence=CostConfidence.UNKNOWN,
        source="native_session",
        source_digest=SOURCE_DIGEST,
        observed_at=NOW,
        reason_code=reason_code,
    )
