"""Fail-closed deterministic structured-route admission."""

from __future__ import annotations

from gigaloom.execution.route_advisor.models import (
    RouteAdmissionEvaluation,
    RouteCostKnowledge,
    RouteFactState,
    RouteRejectionCode,
    StructuredRouteCandidateV1,
)
from gigaloom.execution.route_advisor.requirements import RouteRequirementsV1


def admit_route(
    requirements: RouteRequirementsV1,
    candidate: StructuredRouteCandidateV1,
) -> RouteAdmissionEvaluation:
    """Evaluate every required route fact without fallback or execution."""
    reasons: list[RouteRejectionCode] = []
    _append_fact_reason(
        reasons,
        candidate.profile_admission,
        rejected=RouteRejectionCode.PROFILE_NOT_ADMITTED,
        unknown=RouteRejectionCode.PROFILE_ADMISSION_UNKNOWN,
    )
    _append_fact_reason(
        reasons,
        candidate.route_presence,
        rejected=RouteRejectionCode.STRUCTURED_ROUTE_MISSING,
        unknown=RouteRejectionCode.STRUCTURED_ROUTE_PRESENCE_UNKNOWN,
    )
    _append_fact_reason(
        reasons,
        candidate.executable_readiness,
        rejected=RouteRejectionCode.EXECUTABLE_NOT_READY,
        unknown=RouteRejectionCode.EXECUTABLE_READINESS_UNKNOWN,
    )
    _append_fact_reason(
        reasons,
        candidate.version_readiness,
        rejected=RouteRejectionCode.VERSION_NOT_READY,
        unknown=RouteRejectionCode.VERSION_READINESS_UNKNOWN,
    )
    _append_fact_reason(
        reasons,
        candidate.capability_snapshot_state,
        rejected=RouteRejectionCode.CAPABILITY_SNAPSHOT_STALE,
        unknown=RouteRejectionCode.CAPABILITY_SNAPSHOT_UNKNOWN,
    )

    capability_states = {
        item.capability_id: item.state for item in candidate.capabilities
    }
    for capability_id in requirements.required_capabilities:
        state = capability_states.get(capability_id)
        if state is None or state is RouteFactState.REJECTED:
            _append_unique(reasons, RouteRejectionCode.REQUIRED_CAPABILITY_MISSING)
        elif state is RouteFactState.UNKNOWN:
            _append_unique(reasons, RouteRejectionCode.REQUIRED_CAPABILITY_UNKNOWN)

    if (
        requirements.required_transport_classes
        and candidate.transport_class not in requirements.required_transport_classes
    ):
        reasons.append(RouteRejectionCode.TRANSPORT_CLASS_MISMATCH)
    if (
        candidate.platform_support
        and requirements.platform not in candidate.platform_support
    ):
        reasons.append(RouteRejectionCode.PLATFORM_INCOMPATIBLE)
    if requirements.workspace_policy not in candidate.workspace_policies:
        reasons.append(RouteRejectionCode.WORKSPACE_POLICY_INCOMPATIBLE)
    if requirements.network_policy not in candidate.network_policies:
        reasons.append(RouteRejectionCode.NETWORK_POLICY_INCOMPATIBLE)

    if requirements.required_host_id is not None:
        if candidate.host_id is None:
            reasons.append(RouteRejectionCode.HOST_IDENTITY_UNKNOWN)
        elif candidate.host_id != requirements.required_host_id:
            reasons.append(RouteRejectionCode.HOST_INCOMPATIBLE)

    _append_fact_reason(
        reasons,
        candidate.account_state,
        rejected=RouteRejectionCode.ACCOUNT_IDENTITY_DRIFTED,
        unknown=RouteRejectionCode.ACCOUNT_IDENTITY_UNRESOLVED,
    )
    if (
        requirements.required_account_digest is not None
        and candidate.account_state is RouteFactState.SATISFIED
        and candidate.account_digest != requirements.required_account_digest
    ):
        _append_unique(reasons, RouteRejectionCode.ACCOUNT_IDENTITY_DRIFTED)
    _append_fact_reason(
        reasons,
        candidate.policy_state,
        rejected=RouteRejectionCode.POLICY_DENIED,
        unknown=RouteRejectionCode.POLICY_UNKNOWN,
    )
    _append_fact_reason(
        reasons,
        candidate.budget_state,
        rejected=RouteRejectionCode.BUDGET_NOT_ADMITTED,
        unknown=RouteRejectionCode.BUDGET_ADMISSION_UNKNOWN,
    )
    if (
        requirements.require_known_cost
        and candidate.cost.knowledge is RouteCostKnowledge.UNKNOWN
    ):
        reasons.append(RouteRejectionCode.MONETARY_COST_UNKNOWN)
    _append_fact_reason(
        reasons,
        candidate.project_location_state,
        rejected=RouteRejectionCode.PROJECT_LOCATION_UNRESOLVED,
        unknown=RouteRejectionCode.PROJECT_LOCATION_UNKNOWN,
    )
    if requirements.require_sealed_evaluation:
        _append_fact_reason(
            reasons,
            candidate.sealed_evaluation_state,
            rejected=RouteRejectionCode.SEALED_EVALUATION_MISSING,
            unknown=RouteRejectionCode.SEALED_EVALUATION_UNKNOWN,
        )
    if requirements.require_session_portability:
        _append_fact_reason(
            reasons,
            candidate.session_portability_state,
            rejected=RouteRejectionCode.SESSION_PORTABILITY_UNPROVEN,
            unknown=RouteRejectionCode.SESSION_PORTABILITY_UNKNOWN,
        )
    return RouteAdmissionEvaluation(
        candidate=candidate,
        rejection_codes=tuple(reasons),
    )


def _append_fact_reason(
    reasons: list[RouteRejectionCode],
    state: RouteFactState,
    *,
    rejected: RouteRejectionCode,
    unknown: RouteRejectionCode,
) -> None:
    if state is RouteFactState.REJECTED:
        _append_unique(reasons, rejected)
    elif state is RouteFactState.UNKNOWN:
        _append_unique(reasons, unknown)


def _append_unique(
    reasons: list[RouteRejectionCode],
    reason: RouteRejectionCode,
) -> None:
    if reason not in reasons:
        reasons.append(reason)


__all__ = ["admit_route"]
