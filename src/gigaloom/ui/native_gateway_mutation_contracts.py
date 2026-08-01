"""Mutation contracts owned by the Native Agent Gateway composition."""

from __future__ import annotations

from gigaloom.ui.mutation_contract_models import (
    ConformanceBehavior,
    ConformanceEvidence,
    EnforcementControl,
    MutationClass,
    MutationRouteContract,
)


CONFORMANCE_EVIDENCE = (
    ConformanceEvidence(
        id="native_gateway.projects",
        behaviors=frozenset(
            {
                ConformanceBehavior.ALLOW,
                ConformanceBehavior.DENY,
                ConformanceBehavior.STALE_OR_REBOUND,
                ConformanceBehavior.REDACTION,
            }
        ),
        test_nodes=(
            "tests/harness/test_project_catalog_web_api.py::test_project_catalog_web_crud_detail_and_profile_workspace",
            "tests/harness/test_project_catalog_web_api.py::test_project_catalog_web_rejects_stale_mutations",
        ),
    ),
    ConformanceEvidence(
        id="native_gateway.routing",
        behaviors=frozenset(
            {
                ConformanceBehavior.ALLOW,
                ConformanceBehavior.DENY,
                ConformanceBehavior.STALE_OR_REBOUND,
                ConformanceBehavior.REDACTION,
            }
        ),
        test_nodes=(
            "tests/harness/test_route_advisor_api.py::test_web_inspects_eligible_rejected_selected_and_manual_override",
            "tests/harness/test_route_advisor_integration.py::test_manual_override_requires_new_confirmation_for_exact_route",
        ),
    ),
    ConformanceEvidence(
        id="native_gateway.mcp_apps",
        behaviors=frozenset(
            {
                ConformanceBehavior.ALLOW,
                ConformanceBehavior.DENY,
                ConformanceBehavior.REDACTION,
            }
        ),
        test_nodes=(
            "tests/harness/test_mcp_apps.py::test_backend_router_returns_descriptor_resource_choice_and_teardown",
            "tests/harness/test_mcp_apps.py::test_backend_router_returns_typed_fallback_and_security_errors",
        ),
    ),
)

_AUTH = ("ui.auth_boundary", "ui.redaction_boundary")
_READ = (*_AUTH, "projection.allow")
_PROJECTS = (*_AUTH, "native_gateway.projects")
_ROUTING = (*_AUTH, "native_gateway.routing")
_MCP_APPS = (*_AUTH, "native_gateway.mcp_apps")


def _contract(
    method: str,
    path: str,
    mutation_class: MutationClass,
    control: EnforcementControl,
    owner: str | None,
    evidence: tuple[str, ...],
) -> MutationRouteContract:
    return MutationRouteContract(
        method=method,
        path=path,
        mutation_class=mutation_class,
        control=control,
        enforcement_owner=owner,
        permission_actions=(),
        evidence_ids=evidence,
    )


MUTATION_ROUTE_CONTRACTS = (
    *(
        _contract(
            "POST",
            path,
            MutationClass.READ_ONLY,
            EnforcementControl.AUTHENTICATED_PROJECTION,
            None,
            _READ,
        )
        for path in (
            "/api/project-catalog/{catalog_project_id}/relocation-preview",
            "/api/route-decisions/recommend",
        )
    ),
    *(
        _contract(
            "POST",
            path,
            MutationClass.LOCAL_STATE,
            EnforcementControl.AUTHENTICATED_LOCAL_STATE,
            "projects.catalog.local_state",
            _PROJECTS,
        )
        for path in (
            "/api/project-catalog",
            "/api/project-catalog/sessions/{session_id}/move",
            "/api/project-catalog/{catalog_project_id}/launch-profiles",
        )
    ),
    *(
        _contract(
            method,
            path,
            MutationClass.LOCAL_STATE,
            EnforcementControl.OPTIMISTIC_LOCAL_STATE,
            "projects.catalog.exact_revision",
            _PROJECTS,
        )
        for method in ("PATCH", "DELETE")
        for path in (
            "/api/project-catalog/{catalog_project_id}",
            "/api/project-launch-profiles/{launch_profile_id}",
        )
    ),
    _contract(
        "POST",
        "/api/project-catalog/{catalog_project_id}/relocate",
        MutationClass.LOCAL_STATE,
        EnforcementControl.OPTIMISTIC_LOCAL_STATE,
        "projects.catalog.relocation_preview",
        _PROJECTS,
    ),
    _contract(
        "POST",
        "/api/route-decisions/{route_decision_id}/override",
        MutationClass.LOCAL_STATE,
        EnforcementControl.OPTIMISTIC_LOCAL_STATE,
        "review.route_decisions.eligible_override",
        _ROUTING,
    ),
    *(
        _contract(
            "POST",
            path,
            MutationClass.LOCAL_STATE,
            EnforcementControl.AUTHENTICATED_LOCAL_STATE,
            "tools.mcp_apps.ephemeral_frame",
            _MCP_APPS,
        )
        for path in (
            "/api/mcp-apps/frames",
            "/api/mcp-apps/frames/{instance_id}/messages",
        )
    ),
    _contract(
        "DELETE",
        "/api/mcp-apps/frames/{instance_id}",
        MutationClass.LOCAL_STATE,
        EnforcementControl.AUTHENTICATED_LOCAL_STATE,
        "tools.mcp_apps.ephemeral_frame",
        _MCP_APPS,
    ),
)


__all__ = ["CONFORMANCE_EVIDENCE", "MUTATION_ROUTE_CONTRACTS"]
