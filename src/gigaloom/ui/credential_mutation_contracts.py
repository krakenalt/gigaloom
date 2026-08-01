"""Mutation contracts for revision-bound credential operator actions."""

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
        id="credential.operator",
        behaviors=frozenset(ConformanceBehavior),
        test_nodes=(
            "tests/harness/credentials/test_web.py::test_credential_operator_http_is_revision_bound_and_content_free",
            "tests/harness/credentials/test_web.py::test_credential_operator_projects_expiry_and_idempotent_revocation",
        ),
    ),
)

_EVIDENCE = (
    "ui.auth_boundary",
    "ui.redaction_boundary",
    "credential.operator",
)

MUTATION_ROUTE_CONTRACTS = tuple(
    MutationRouteContract(
        method="POST",
        path=path,
        mutation_class=MutationClass.LOCAL_STATE,
        control=EnforcementControl.OPTIMISTIC_LOCAL_STATE,
        enforcement_owner="credential_operator.revision_bound_action",
        permission_actions=(),
        evidence_ids=_EVIDENCE,
    )
    for path in (
        "/api/credentials/leases",
        "/api/credentials/leases/{lease_id}/revoke",
    )
)


__all__ = ["CONFORMANCE_EVIDENCE", "MUTATION_ROUTE_CONTRACTS"]
