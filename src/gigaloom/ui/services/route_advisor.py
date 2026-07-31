"""Application composition for Route Advisor Web inspection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from gigaloom.execution.api import (
    RouteAdvisorApplicationService,
    RouteRecommendationQueryV1,
)
from gigaloom.review.api import (
    RouteDecisionBindingsV1,
    RouteDecisionReceiptV1,
    RouteDecisionRepository,
    create_route_decision_receipt,
    override_route_decision_receipt,
    verify_route_decision_receipt,
)


@dataclass(frozen=True)
class RouteAdvisorWebService:
    """Persist and inspect content-free recommendations without execution."""

    advisor: RouteAdvisorApplicationService
    repository: RouteDecisionRepository
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def recommend(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteDecisionReceiptV1:
        """Create one immutable recommendation from current backend facts."""
        result = self.advisor.recommend(query)
        snapshot = result.snapshot
        receipt = create_route_decision_receipt(
            result.advice,
            RouteDecisionBindingsV1(
                task_digest=query.task_digest,
                context_manifest_digest=query.context_manifest_digest,
                project_catalog_digest=snapshot.project_catalog_digest,
                launch_profile_digest=snapshot.requirements.launch_profile_digest,
                capability_catalog_digest=snapshot.capability_catalog_digest,
                cost_policy_digest=snapshot.cost_policy_digest,
            ),
            created_at=_timestamp(self.clock()),
        )
        return self.repository.save(receipt)

    def inspect(self, route_decision_id: str) -> RouteDecisionReceiptV1:
        """Return one integrity-checked immutable decision."""
        return verify_route_decision_receipt(self.repository.get(route_decision_id))

    def override(
        self,
        route_decision_id: str,
        *,
        route_id: str,
        reason_code: str,
    ) -> RouteDecisionReceiptV1:
        """Persist a distinct eligible-only operator selection."""
        receipt = override_route_decision_receipt(
            self.inspect(route_decision_id),
            route_id=route_id,
            reason_code=reason_code,
            created_at=_timestamp(self.clock()),
        )
        return self.repository.save(receipt)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Route Advisor clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = ["RouteAdvisorWebService"]
