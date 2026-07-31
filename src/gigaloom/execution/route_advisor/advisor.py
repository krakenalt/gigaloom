"""Pure Route Advisor orchestration with no provider execution."""

from __future__ import annotations

from typing import Protocol

from gigaloom.execution.route_advisor.admission import admit_route
from gigaloom.execution.route_advisor.candidates import canonicalize_candidates
from gigaloom.execution.route_advisor.errors import RouteRankingError
from gigaloom.execution.route_advisor.models import (
    EligibleRouteEvidence,
    RejectedRouteEvidence,
    RouteAdviceOutcome,
    RouteAdviceV1,
    RouteAdmissionEvaluation,
    StructuredRouteCandidateV1,
)
from gigaloom.execution.route_advisor.requirements import RouteRequirementsV1
from gigaloom.execution.route_advisor.scoring import PolicyRankerV1


class RouteRanker(Protocol):
    """Minimal deterministic ranker port."""

    ranker_id: str
    ranker_version: str

    def rank(
        self,
        requirements: RouteRequirementsV1,
        evaluations: tuple[RouteAdmissionEvaluation, ...],
    ) -> tuple[EligibleRouteEvidence, ...]: ...


def advise_routes(
    requirements: RouteRequirementsV1,
    candidates: tuple[StructuredRouteCandidateV1, ...],
    *,
    ranker: RouteRanker | None = None,
) -> RouteAdviceV1:
    """Admit and rank snapshots without probing or starting a route."""
    selected_ranker = ranker or PolicyRankerV1()
    evaluations = tuple(
        admit_route(requirements, candidate)
        for candidate in canonicalize_candidates(candidates)
    )
    eligible = tuple(item for item in evaluations if item.eligible)
    rejected = tuple(
        RejectedRouteEvidence(
            route_id=item.candidate.route_id,
            agent_id=item.candidate.agent_id,
            reason_codes=item.rejection_codes,
        )
        for item in evaluations
        if not item.eligible
    )
    if not eligible:
        return RouteAdviceV1(
            eligible_routes=(),
            rejected_routes=rejected,
            recommended_route_id=None,
            ranker_id=selected_ranker.ranker_id,
            ranker_version=selected_ranker.ranker_version,
            outcome=RouteAdviceOutcome.NEEDS_HUMAN,
        )
    try:
        ranked = selected_ranker.rank(requirements, eligible)
    except RouteRankingError:
        ranked = tuple(
            EligibleRouteEvidence(
                route_id=item.candidate.route_id,
                agent_id=item.candidate.agent_id,
                profile_digest=item.candidate.profile_digest,
                capability_snapshot_digest=(item.candidate.capability_snapshot_digest),
                account_digest=item.candidate.account_digest or "0" * 64,
                transport_class=item.candidate.transport_class,
                cost=item.candidate.cost,
                compatibility_grade=item.candidate.compatibility_grade,
                policy_priority=item.candidate.policy_priority,
                explicit_preference_match=False,
                exact_capability_match=False,
                latency=item.candidate.latency,
            )
            for item in eligible
        )
        return RouteAdviceV1(
            eligible_routes=ranked,
            rejected_routes=rejected,
            recommended_route_id=None,
            ranker_id=selected_ranker.ranker_id,
            ranker_version=selected_ranker.ranker_version,
            outcome=RouteAdviceOutcome.NEEDS_HUMAN,
        )
    return RouteAdviceV1(
        eligible_routes=ranked,
        rejected_routes=rejected,
        recommended_route_id=ranked[0].route_id,
        ranker_id=selected_ranker.ranker_id,
        ranker_version=selected_ranker.ranker_version,
        outcome=RouteAdviceOutcome.RECOMMENDED,
    )


__all__ = ["RouteRanker", "advise_routes"]
