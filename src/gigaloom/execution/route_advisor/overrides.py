"""Eligible-only manual Route Advisor overrides."""

from dataclasses import replace

from gigaloom.execution.route_advisor.errors import RouteOverrideError
from gigaloom.execution.route_advisor.models import (
    RouteAdviceOutcome,
    RouteAdviceV1,
    RouteOverrideV1,
)


def apply_route_override(
    advice: RouteAdviceV1,
    *,
    route_id: str,
    reason_code: str,
    created_at: str,
) -> RouteAdviceV1:
    """Select one already eligible route and preserve the original ranking."""
    eligible_ids = {item.route_id for item in advice.eligible_routes}
    if route_id not in eligible_ids:
        raise RouteOverrideError("manual override route is not eligible")
    override = RouteOverrideV1(
        route_id=route_id,
        reason_code=reason_code,
        created_at=created_at,
    )
    return replace(
        advice,
        recommended_route_id=route_id,
        outcome=RouteAdviceOutcome.RECOMMENDED,
        override=override,
    )


__all__ = ["apply_route_override"]
