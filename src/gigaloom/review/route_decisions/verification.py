"""Integrity and freshness verification for route decision receipts."""

from gigaloom.review.route_decisions.codec import (
    route_decision_receipt_from_dict,
    route_decision_receipt_to_dict,
)
from gigaloom.review.route_decisions.models import (
    RouteDecisionBindingsV1,
    RouteDecisionReceiptV1,
    RouteDecisionVerificationError,
)


def verify_route_decision_receipt(
    receipt: RouteDecisionReceiptV1,
    *,
    expected_bindings: RouteDecisionBindingsV1 | None = None,
) -> RouteDecisionReceiptV1:
    """Verify canonical integrity and optionally reject stale bindings."""
    verified = route_decision_receipt_from_dict(route_decision_receipt_to_dict(receipt))
    if expected_bindings is None:
        return verified
    stale_fields = tuple(
        field_name
        for field_name in (
            "task_digest",
            "context_manifest_digest",
            "project_catalog_digest",
            "launch_profile_digest",
            "capability_catalog_digest",
            "cost_policy_digest",
        )
        if getattr(verified.bindings, field_name)
        != getattr(expected_bindings, field_name)
    )
    if stale_fields:
        raise RouteDecisionVerificationError(
            "route decision bindings are stale: " + ",".join(stale_fields)
        )
    return verified


__all__ = ["verify_route_decision_receipt"]
