"""Pure pre-spawn monetary admission for explicit route prices."""

from __future__ import annotations

from gigaloom.contracts.cost import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetPolicy,
    BudgetPolicyKind,
    CostConfidence,
    CostObservation,
)


def evaluate_budget_admission(
    *,
    admission_id: str,
    policy: BudgetPolicy,
    requested_cost: CostObservation,
    created_at: str,
    price_source_digest: str | None = None,
) -> BudgetAdmission:
    """Evaluate one route without storage, model calls, or account switching."""
    decision = BudgetAdmissionDecision.DENIED
    reason_code = "budget_policy_denied"
    admitted_price_digest: str | None = None

    if policy.kind is BudgetPolicyKind.UNLIMITED:
        decision = BudgetAdmissionDecision.ADMITTED
        reason_code = "explicit_unlimited_policy"
    elif requested_cost.confidence is CostConfidence.UNKNOWN:
        reason_code = "unknown_monetary_cost"
    elif requested_cost.currency != policy.currency:
        reason_code = "currency_mismatch"
    elif requested_cost.amount is None or policy.amount is None:
        reason_code = "missing_monetary_amount"
    elif requested_cost.amount > policy.amount:
        reason_code = "request_exceeds_policy"
    elif price_source_digest is None:
        reason_code = "price_source_missing"
    elif price_source_digest != requested_cost.source_digest:
        reason_code = "price_source_mismatch"
    else:
        decision = BudgetAdmissionDecision.ADMITTED
        reason_code = "within_policy"
        admitted_price_digest = price_source_digest

    return BudgetAdmission(
        id=admission_id,
        policy=policy,
        decision=decision,
        route_class=requested_cost.route_class,
        requested_cost=requested_cost,
        reason_code=reason_code,
        created_at=created_at,
        price_source_digest=admitted_price_digest,
    )


__all__ = ["evaluate_budget_admission"]
