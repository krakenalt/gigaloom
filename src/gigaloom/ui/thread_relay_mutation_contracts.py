"""Mutation contracts owned by the reviewed thread-relay composition."""

from gigaloom.ui.mutation_contract_models import (
    ConformanceBehavior,
    ConformanceEvidence,
    EnforcementControl,
    MutationClass,
    MutationRouteContract,
)


CONFORMANCE_EVIDENCE = (
    ConformanceEvidence(
        id="thread_relay.explicit_user_send",
        behaviors=frozenset(
            {
                ConformanceBehavior.ALLOW,
                ConformanceBehavior.DENY,
                ConformanceBehavior.REDACTION,
            }
        ),
        test_nodes=(
            "tests/harness/thread_relay/test_routes.py::test_route_local_api_lists_reads_previews_sends_and_reports_status",
            "tests/harness/thread_relay/test_routes.py::test_preview_content_echo_is_rejected_before_mutation",
        ),
    ),
)

_AUTH = ("ui.auth_boundary", "ui.redaction_boundary")
_READ = (*_AUTH, "projection.allow")

MUTATION_ROUTE_CONTRACTS = (
    *(
        MutationRouteContract(
            method="POST",
            path=path,
            mutation_class=MutationClass.READ_ONLY,
            control=EnforcementControl.AUTHENTICATED_PROJECTION,
            enforcement_owner=None,
            permission_actions=(),
            evidence_ids=_READ,
        )
        for path in (
            "/api/gateway/routes/{route_id}/preflight",
            "/api/thread-relay/deliveries/preview",
        )
    ),
    MutationRouteContract(
        method="POST",
        path="/api/thread-relay/deliveries",
        mutation_class=MutationClass.GOVERNED_EXTERNAL_EFFECT,
        control=EnforcementControl.EXPLICIT_OPERATOR_ACTION,
        enforcement_owner="thread_relay.explicit_user_send",
        permission_actions=(),
        evidence_ids=(*_AUTH, "thread_relay.explicit_user_send"),
    ),
)


__all__ = ["CONFORMANCE_EVIDENCE", "MUTATION_ROUTE_CONTRACTS"]
