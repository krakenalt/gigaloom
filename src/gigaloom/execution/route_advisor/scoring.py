"""Deterministic policy ranking for eligible structured routes."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from gigaloom.execution.route_advisor.errors import RouteRankingError
from gigaloom.execution.route_advisor.models import (
    EligibleRouteEvidence,
    RouteAdmissionEvaluation,
    RouteFactState,
)
from gigaloom.execution.route_advisor.requirements import RouteRequirementsV1


POLICY_RANKER_ID = "policy_ranker_v1"
POLICY_RANKER_VERSION = "1"


class PolicyRankerV1:
    """Rank eligible routes using only explicit comparable evidence."""

    ranker_id = POLICY_RANKER_ID
    ranker_version = POLICY_RANKER_VERSION

    def rank(
        self,
        requirements: RouteRequirementsV1,
        evaluations: tuple[RouteAdmissionEvaluation, ...],
    ) -> tuple[EligibleRouteEvidence, ...]:
        """Return a total stable order or fail closed on ineligible input."""
        if not evaluations or any(not item.eligible for item in evaluations):
            raise RouteRankingError("ranker requires a non-empty eligible route set")
        evidence = tuple(_eligible_evidence(requirements, item) for item in evaluations)
        cost_comparable = _cost_is_comparable(evidence)
        latency_comparable = _latency_is_comparable(evidence)
        ranked = sorted(
            evidence,
            key=lambda item: (
                -int(item.explicit_preference_match),
                -int(item.exact_capability_match),
                -int(item.compatibility_grade),
                item.policy_priority,
                -_headroom(item, comparable=cost_comparable),
                _latency(item, comparable=latency_comparable),
                item.route_id,
            ),
        )
        return tuple(
            replace(item, rank=index) for index, item in enumerate(ranked, start=1)
        )


def _eligible_evidence(
    requirements: RouteRequirementsV1,
    evaluation: RouteAdmissionEvaluation,
) -> EligibleRouteEvidence:
    candidate = evaluation.candidate
    if candidate.account_digest is None:
        raise RouteRankingError("eligible route has no resolved account digest")
    satisfied_capabilities = {
        item.capability_id
        for item in candidate.capabilities
        if item.state is RouteFactState.SATISFIED
    }
    return EligibleRouteEvidence(
        route_id=candidate.route_id,
        agent_id=candidate.agent_id,
        profile_digest=candidate.profile_digest,
        capability_snapshot_digest=candidate.capability_snapshot_digest,
        account_digest=candidate.account_digest,
        transport_class=candidate.transport_class,
        cost=candidate.cost,
        compatibility_grade=candidate.compatibility_grade,
        policy_priority=candidate.policy_priority,
        explicit_preference_match=(
            candidate.route_id == requirements.preferred_route_id
        ),
        exact_capability_match=(
            satisfied_capabilities == set(requirements.required_capabilities)
        ),
        latency=candidate.latency,
    )


def _cost_is_comparable(evidence: tuple[EligibleRouteEvidence, ...]) -> bool:
    currencies = {item.cost.currency for item in evidence}
    return (
        len(currencies) == 1
        and None not in currencies
        and all(item.cost.headroom is not None for item in evidence)
    )


def _latency_is_comparable(evidence: tuple[EligibleRouteEvidence, ...]) -> bool:
    groups = {
        item.latency.comparison_group for item in evidence if item.latency is not None
    }
    return len(groups) == 1 and all(item.latency is not None for item in evidence)


def _headroom(item: EligibleRouteEvidence, *, comparable: bool) -> Decimal:
    if not comparable or item.cost.headroom is None:
        return Decimal(0)
    return item.cost.headroom


def _latency(item: EligibleRouteEvidence, *, comparable: bool) -> int:
    if not comparable or item.latency is None:
        return 0
    return item.latency.p95_milliseconds


__all__ = ["POLICY_RANKER_ID", "POLICY_RANKER_VERSION", "PolicyRankerV1"]
