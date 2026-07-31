"""Immutable eligible-only overrides for persisted route decisions."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.review.route_decisions.codec import create_route_decision_receipt
from gigaloom.review.route_decisions.models import (
    RouteDecisionEligibleRouteV1,
    RouteDecisionOutcome,
    RouteDecisionOverrideError,
    RouteDecisionOverrideV1,
    RouteDecisionReceiptV1,
    RouteDecisionRejectedRouteV1,
)
from gigaloom.review.route_decisions.verification import (
    verify_route_decision_receipt,
)


@dataclass(frozen=True)
class _OverriddenAdvice:
    eligible_routes: tuple[RouteDecisionEligibleRouteV1, ...]
    rejected_routes: tuple[RouteDecisionRejectedRouteV1, ...]
    recommended_route_id: str
    ranker_id: str
    ranker_version: str
    override: RouteDecisionOverrideV1
    outcome: RouteDecisionOutcome


def override_route_decision_receipt(
    receipt: RouteDecisionReceiptV1,
    *,
    route_id: str,
    reason_code: str,
    created_at: str,
) -> RouteDecisionReceiptV1:
    """Create a new receipt selecting one route already proven eligible."""
    verified = verify_route_decision_receipt(receipt)
    if route_id not in {item.route_id for item in verified.eligible_routes}:
        raise RouteDecisionOverrideError("manual override route is not eligible")
    advice = _OverriddenAdvice(
        eligible_routes=verified.eligible_routes,
        rejected_routes=verified.rejected_routes,
        recommended_route_id=route_id,
        ranker_id=verified.ranker_id,
        ranker_version=verified.ranker_version,
        override=RouteDecisionOverrideV1(
            route_id=route_id,
            reason_code=reason_code,
            created_at=created_at,
        ),
        outcome=RouteDecisionOutcome.RECOMMENDED,
    )
    return create_route_decision_receipt(
        advice,
        verified.bindings,
        created_at=created_at,
    )


__all__ = ["override_route_decision_receipt"]
