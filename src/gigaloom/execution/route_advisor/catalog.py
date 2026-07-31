"""Public capability-catalog adapter for Route Advisor candidates."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.harnesses.api import (
    CapabilityCatalogFactState,
    CapabilityCatalogV1,
    CapabilitySnapshotState,
)

from .models import (
    CapabilityEvidence,
    CompatibilityGrade,
    LatencyEvidence,
    RouteCostEvidence,
    RouteFactState,
    StructuredRouteCandidateV1,
)


@dataclass(frozen=True)
class RouteOperationalFactsV1:
    """Current policy and readiness facts outside the capability catalog."""

    route_id: str
    workspace_policies: tuple[str, ...]
    network_policies: tuple[str, ...]
    profile_admission: RouteFactState
    executable_readiness: RouteFactState
    version_readiness: RouteFactState
    account_state: RouteFactState
    account_digest: str | None
    policy_state: RouteFactState
    budget_state: RouteFactState
    project_location_state: RouteFactState
    sealed_evaluation_state: RouteFactState
    session_portability_state: RouteFactState
    cost: RouteCostEvidence
    compatibility_grade: CompatibilityGrade
    policy_priority: int
    host_id: str | None = None
    latency: LatencyEvidence | None = None

    def __post_init__(self) -> None:
        if not self.route_id:
            raise ValueError("route operational facts require a route id")
        for values, field_name in (
            (self.workspace_policies, "workspace policies"),
            (self.network_policies, "network policies"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"{field_name} must be sorted and unique")
        for value, field_name in (
            (self.profile_admission, "profile admission"),
            (self.executable_readiness, "executable readiness"),
            (self.version_readiness, "version readiness"),
            (self.account_state, "account state"),
            (self.policy_state, "policy state"),
            (self.budget_state, "budget state"),
            (self.project_location_state, "project location state"),
            (self.sealed_evaluation_state, "sealed evaluation state"),
            (self.session_portability_state, "session portability state"),
        ):
            if not isinstance(value, RouteFactState):
                raise ValueError(f"{field_name} is invalid")
        if not isinstance(self.cost, RouteCostEvidence):
            raise ValueError("route cost evidence is invalid")
        if not isinstance(self.compatibility_grade, CompatibilityGrade):
            raise ValueError("route compatibility grade is invalid")


def candidates_from_capability_catalog(
    catalog: CapabilityCatalogV1,
    operational_facts: tuple[RouteOperationalFactsV1, ...],
) -> tuple[StructuredRouteCandidateV1, ...]:
    """Compose all catalog routes without hiding unknown or rejected facts."""
    if not isinstance(catalog, CapabilityCatalogV1):
        raise ValueError("capability catalog is invalid")
    facts_by_route = {item.route_id: item for item in operational_facts}
    if len(facts_by_route) != len(operational_facts):
        raise ValueError("route operational facts must have unique route ids")
    catalog_route_ids = {item.descriptor.route_id for item in catalog.routes}
    if set(facts_by_route) != catalog_route_ids:
        raise ValueError("operational facts must cover every capability catalog route")

    candidates: list[StructuredRouteCandidateV1] = []
    for route in catalog.routes:
        descriptor = route.descriptor
        facts = facts_by_route[descriptor.route_id]
        candidates.append(
            StructuredRouteCandidateV1(
                route_id=descriptor.route_id,
                agent_id=descriptor.agent_id,
                profile_digest=descriptor.profile_digest,
                transport_class=descriptor.transport_kind,
                capabilities=tuple(
                    CapabilityEvidence(
                        item.capability_id,
                        _capability_fact_state(item.state),
                    )
                    for item in route.capabilities
                ),
                platform_support=descriptor.platform_support,
                workspace_policies=facts.workspace_policies,
                network_policies=facts.network_policies,
                profile_admission=facts.profile_admission,
                route_presence=RouteFactState.SATISFIED,
                executable_readiness=facts.executable_readiness,
                version_readiness=facts.version_readiness,
                capability_snapshot_state=_snapshot_fact_state(route.snapshot_state),
                capability_snapshot_digest=route.capability_snapshot_digest,
                account_state=facts.account_state,
                account_digest=facts.account_digest,
                policy_state=facts.policy_state,
                budget_state=facts.budget_state,
                project_location_state=facts.project_location_state,
                sealed_evaluation_state=facts.sealed_evaluation_state,
                session_portability_state=facts.session_portability_state,
                cost=facts.cost,
                compatibility_grade=facts.compatibility_grade,
                policy_priority=facts.policy_priority,
                host_id=facts.host_id,
                latency=facts.latency,
            )
        )
    return tuple(candidates)


def _capability_fact_state(state: CapabilityCatalogFactState) -> RouteFactState:
    return {
        CapabilityCatalogFactState.READY: RouteFactState.SATISFIED,
        CapabilityCatalogFactState.UNSUPPORTED: RouteFactState.REJECTED,
        CapabilityCatalogFactState.UNKNOWN: RouteFactState.UNKNOWN,
    }[state]


def _snapshot_fact_state(state: CapabilitySnapshotState) -> RouteFactState:
    return {
        CapabilitySnapshotState.CURRENT: RouteFactState.SATISFIED,
        CapabilitySnapshotState.STALE: RouteFactState.REJECTED,
        CapabilitySnapshotState.UNKNOWN: RouteFactState.UNKNOWN,
    }[state]


__all__ = [
    "RouteOperationalFactsV1",
    "candidates_from_capability_catalog",
]
