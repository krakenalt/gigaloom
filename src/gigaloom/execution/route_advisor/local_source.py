"""Fail-closed local composition for Route Advisor read models."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from gigaloom.harnesses.api import (
    AgentProfileV1,
    bind_structured_route_descriptors,
    build_capability_catalog,
)
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
)

from .application import (
    RouteRecommendationQueryV1,
    RouteRecommendationSnapshotV1,
)
from .catalog import RouteOperationalFactsV1, candidates_from_capability_catalog
from .models import (
    CompatibilityGrade,
    RouteCostEvidence,
    RouteCostKnowledge,
    RouteFactState,
)
from .requirements import RouteRequirementsV1


class LocalRouteRecommendationSource:
    """Read current local facts without promoting discovery to admission."""

    def __init__(
        self,
        data_dir: str | Path,
        profiles: tuple[AgentProfileV1, ...],
    ) -> None:
        root = Path(data_dir) / "projects"
        self._projects = FilesystemProjectCatalogRepository(root / "catalog")
        self._launch_profiles = FilesystemLaunchProfileRepository(
            root / "launch_profiles"
        )
        self._profiles = profiles

    def snapshot(
        self,
        query: RouteRecommendationQueryV1,
    ) -> RouteRecommendationSnapshotV1:
        """Resolve one deterministic snapshot with unknown facts kept unknown."""
        project = self._projects.get(query.project_id)
        launch_profile_digest: str | None = None
        if query.launch_profile_id is not None:
            launch_profile = self._launch_profiles.get(query.launch_profile_id)
            if launch_profile.catalog_project_id != project.catalog_project_id:
                raise ValueError("launch profile belongs to another project")
            launch_profile_digest = launch_profile.digest

        catalog = build_capability_catalog(
            bind_structured_route_descriptors(self._profiles)
        )
        project_location_state = (
            RouteFactState.SATISFIED
            if project.state == "active" and project.location.canonical_path is not None
            else RouteFactState.REJECTED
        )
        unknown = RouteFactState.UNKNOWN
        operational_facts = tuple(
            RouteOperationalFactsV1(
                route_id=route.descriptor.route_id,
                workspace_policies=("read_only",),
                network_policies=("denied",),
                profile_admission=unknown,
                executable_readiness=unknown,
                version_readiness=unknown,
                account_state=unknown,
                account_digest=None,
                policy_state=unknown,
                budget_state=unknown,
                project_location_state=project_location_state,
                sealed_evaluation_state=unknown,
                session_portability_state=unknown,
                cost=RouteCostEvidence(RouteCostKnowledge.UNKNOWN),
                compatibility_grade=CompatibilityGrade.DEGRADED,
                policy_priority=index,
            )
            for index, route in enumerate(catalog.routes)
        )
        requirements = RouteRequirementsV1(
            intent=query.intent,
            required_capabilities=query.required_capabilities,
            required_transport_classes=query.required_transport_classes,
            workspace_policy=query.workspace_policy,
            network_policy=query.network_policy,
            cost_policy_ref=query.cost_policy_ref,
            platform=query.platform,
            context_manifest_digest=query.context_manifest_digest,
            project_id=query.project_id,
            launch_profile_digest=launch_profile_digest,
            preferred_route_id=query.preferred_route_id,
            required_host_id=query.required_host_id,
            required_account_digest=query.required_account_digest,
            require_known_cost=query.require_known_cost,
            require_sealed_evaluation=query.require_sealed_evaluation,
            require_session_portability=query.require_session_portability,
        )
        return RouteRecommendationSnapshotV1(
            requirements=requirements,
            candidates=candidates_from_capability_catalog(
                catalog,
                operational_facts,
            ),
            project_catalog_digest=project.digest,
            capability_catalog_digest=catalog.digest,
            cost_policy_digest=_cost_policy_digest(query),
        )


def _cost_policy_digest(query: RouteRecommendationQueryV1) -> str:
    payload = {
        "schema_version": 1,
        "cost_policy_ref": query.cost_policy_ref,
        "require_known_cost": query.require_known_cost,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = ["LocalRouteRecommendationSource"]
