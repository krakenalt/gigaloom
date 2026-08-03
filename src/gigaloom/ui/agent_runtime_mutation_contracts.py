"""Mutation contracts owned by managed Coding Agent runtime composition."""

from __future__ import annotations

from gigaloom.runtime.policy import PermissionAction
from gigaloom.ui.mutation_contract_models import (
    ConformanceBehavior,
    ConformanceEvidence,
    EnforcementControl,
    MutationClass,
    MutationRouteContract,
)


CONFORMANCE_EVIDENCE = (
    ConformanceEvidence(
        id="agent_runtime.installation",
        behaviors=frozenset(ConformanceBehavior),
        test_nodes=(
            "tests/harness/agent_installations/test_agent_registry_web.py::test_background_operation_emits_content_free_progress_and_deep_link",
            "tests/harness/agent_installations/test_agent_registry_web.py::test_confirmed_install_is_bound_to_the_reviewed_registry_revision",
            "tests/harness/agent_installations/test_agent_registry_web.py::test_web_mutations_require_explicit_confirmation",
            "tests/harness/agent_installations/test_agent_registry_web.py::test_bounded_http_routers_expose_preview_operation_and_sse",
        ),
    ),
)

_AUTH = (
    "ui.auth_boundary",
    "ui.redaction_boundary",
    "agent_runtime.installation",
)


def _contract(
    path: str,
    mutation_class: MutationClass,
    control: EnforcementControl,
    owner: str | None,
    *,
    actions: tuple[PermissionAction, ...] = (),
) -> MutationRouteContract:
    return MutationRouteContract(
        method="POST",
        path=path,
        mutation_class=mutation_class,
        control=control,
        enforcement_owner=owner,
        permission_actions=actions,
        evidence_ids=_AUTH,
    )


MUTATION_ROUTE_CONTRACTS = (
    _contract(
        "/api/agent-runtimes/installations/preview",
        MutationClass.READ_ONLY,
        EnforcementControl.AUTHENTICATED_PROJECTION,
        None,
    ),
    _contract(
        "/api/agent-runtimes/installations",
        MutationClass.GOVERNED_EXTERNAL_EFFECT,
        EnforcementControl.REVIEW_BINDING,
        "agent_runtime.install_exact_plan",
        actions=(PermissionAction.PROCESS_SPAWN, PermissionAction.NETWORK_CONNECT),
    ),
    *(
        _contract(
            path,
            MutationClass.LOCAL_STATE,
            EnforcementControl.AUTHENTICATED_LOCAL_STATE,
            "agent_runtime.operation_control",
        )
        for path in (
            "/api/agent-runtimes/installations/{operation_id}/cancel",
            "/api/agent-runtimes/installations/recover",
        )
    ),
    _contract(
        "/api/agent-runtimes/{local_agent_id}/update",
        MutationClass.GOVERNED_EXTERNAL_EFFECT,
        EnforcementControl.EXPLICIT_OPERATOR_ACTION,
        "agent_runtime.confirmed_update",
        actions=(PermissionAction.PROCESS_SPAWN, PermissionAction.NETWORK_CONNECT),
    ),
    _contract(
        "/api/agent-runtimes/{local_agent_id}/probe",
        MutationClass.GOVERNED_EXTERNAL_EFFECT,
        EnforcementControl.EXPLICIT_OPERATOR_ACTION,
        "agent_runtime.isolated_probe",
        actions=(PermissionAction.PROCESS_SPAWN,),
    ),
    _contract(
        "/api/agent-runtimes/{local_agent_id}/activate",
        MutationClass.GOVERNED_EXTERNAL_EFFECT,
        EnforcementControl.EXPLICIT_OPERATOR_ACTION,
        "agent_runtime.confirmed_isolated_activation",
        actions=(PermissionAction.PROCESS_SPAWN,),
    ),
    *(
        _contract(
            path,
            MutationClass.LOCAL_STATE,
            EnforcementControl.EXPLICIT_OPERATOR_ACTION,
            "agent_runtime.confirmed_local_lifecycle",
        )
        for path in (
            "/api/agent-runtimes/{local_agent_id}/rollback",
            "/api/agent-runtimes/{local_agent_id}/remove",
        )
    ),
)


__all__ = ["CONFORMANCE_EVIDENCE", "MUTATION_ROUTE_CONTRACTS"]
