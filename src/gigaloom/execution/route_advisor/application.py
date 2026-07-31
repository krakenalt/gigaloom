"""Application use case for source-backed route recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from gigaloom.execution.route_advisor.advisor import RouteRanker, advise_routes
from gigaloom.execution.route_advisor.models import (
    RouteAdviceV1,
    RouteIntent,
    StructuredRouteCandidateV1,
    _validate_digest,
    _validate_identity,
    _validate_identity_sequence,
)
from gigaloom.execution.route_advisor.requirements import RouteRequirementsV1


@dataclass(frozen=True)
class RouteRecommendationQueryV1:
    """Deterministic task requirements supplied by CLI or Web."""

    project_id: str
    intent: RouteIntent
    task_digest: str
    context_manifest_digest: str
    required_capabilities: tuple[str, ...]
    required_transport_classes: tuple[str, ...]
    workspace_policy: str
    network_policy: str
    cost_policy_ref: str
    platform: str
    launch_profile_id: str | None = None
    preferred_route_id: str | None = None
    required_host_id: str | None = None
    required_account_digest: str | None = None
    require_known_cost: bool = False
    require_sealed_evaluation: bool = False
    require_session_portability: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.intent, RouteIntent):
            raise ValueError("route recommendation intent is invalid")
        for value, field_name in (
            (self.project_id, "project id"),
            (self.workspace_policy, "workspace policy"),
            (self.network_policy, "network policy"),
            (self.cost_policy_ref, "cost policy ref"),
            (self.platform, "platform"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(self.task_digest, field_name="task digest")
        _validate_digest(
            self.context_manifest_digest,
            field_name="context manifest digest",
        )
        _validate_identity_sequence(
            self.required_capabilities,
            field_name="required capabilities",
        )
        _validate_identity_sequence(
            self.required_transport_classes,
            field_name="required transport classes",
        )
        for value, field_name in (
            (self.launch_profile_id, "launch profile id"),
            (self.preferred_route_id, "preferred route id"),
            (self.required_host_id, "required host id"),
        ):
            if value is not None:
                _validate_identity(value, field_name=field_name)
        if self.required_account_digest is not None:
            _validate_digest(
                self.required_account_digest,
                field_name="required account digest",
            )


@dataclass(frozen=True)
class RouteRecommendationSnapshotV1:
    """Current content-free facts resolved by the owning application graph."""

    requirements: RouteRequirementsV1
    candidates: tuple[StructuredRouteCandidateV1, ...]
    project_catalog_digest: str
    capability_catalog_digest: str
    cost_policy_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.requirements, RouteRequirementsV1):
            raise ValueError("route recommendation requirements are invalid")
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(item, StructuredRouteCandidateV1) for item in self.candidates
        ):
            raise ValueError("route recommendation candidates are invalid")
        for value, field_name in (
            (self.project_catalog_digest, "project catalog digest"),
            (self.capability_catalog_digest, "capability catalog digest"),
            (self.cost_policy_digest, "cost policy digest"),
        ):
            _validate_digest(value, field_name=field_name)


class RouteRecommendationSource(Protocol):
    """Resolve one bounded snapshot from authoritative public read models."""

    def snapshot(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteRecommendationSnapshotV1: ...


@dataclass(frozen=True)
class RouteRecommendationResultV1:
    """Advice plus the exact current facts required for receipt creation."""

    query: RouteRecommendationQueryV1
    snapshot: RouteRecommendationSnapshotV1
    advice: RouteAdviceV1


class RouteAdvisorApplicationService:
    """Recommend without probing, provider calls, persistence, or execution."""

    def __init__(
        self,
        source: RouteRecommendationSource,
        *,
        ranker: RouteRanker | None = None,
    ) -> None:
        self._source = source
        self._ranker = ranker

    def recommend(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteRecommendationResultV1:
        """Resolve current facts once and produce deterministic advice."""
        snapshot = self._source.snapshot(query)
        _validate_snapshot_binding(query, snapshot)
        return RouteRecommendationResultV1(
            query=query,
            snapshot=snapshot,
            advice=advise_routes(
                snapshot.requirements,
                snapshot.candidates,
                ranker=self._ranker,
            ),
        )


def _validate_snapshot_binding(
    query: RouteRecommendationQueryV1,
    snapshot: RouteRecommendationSnapshotV1,
) -> None:
    requirements = snapshot.requirements
    mismatches: list[str] = []
    for field_name in (
        "project_id",
        "intent",
        "context_manifest_digest",
        "required_capabilities",
        "required_transport_classes",
        "workspace_policy",
        "network_policy",
        "cost_policy_ref",
        "platform",
        "preferred_route_id",
        "required_host_id",
        "required_account_digest",
        "require_known_cost",
        "require_sealed_evaluation",
        "require_session_portability",
    ):
        if getattr(query, field_name) != getattr(requirements, field_name):
            mismatches.append(field_name)
    if (
        query.launch_profile_id is None
        and requirements.launch_profile_digest is not None
    ):
        mismatches.append("launch_profile_digest")
    if (
        query.launch_profile_id is not None
        and requirements.launch_profile_digest is None
    ):
        mismatches.append("launch_profile_digest")
    if mismatches:
        raise ValueError(
            "route recommendation snapshot does not match query: "
            + ",".join(mismatches)
        )


__all__ = [
    "RouteAdvisorApplicationService",
    "RouteRecommendationQueryV1",
    "RouteRecommendationResultV1",
    "RouteRecommendationSnapshotV1",
    "RouteRecommendationSource",
]
