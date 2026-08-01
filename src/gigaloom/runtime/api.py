"""Public facade for cross-context durable runtime access."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from gigaloom.runtime.models import (
    ApprovalStatus,
    NativeProcessOutputRecord,
    NativeProcessRecord,
    JobAttemptStatus,
    RunStatus,
    parse_run_status,
)

EnforcementLevel: Any
AdmissionDeniedError: Any
BudgetHeadroomExceededError: Any
BudgetLeaseBalance: Any
BudgetLeaseClosedError: Any
BudgetLeaseConflictError: Any
BudgetLeaseExpiredError: Any
BudgetLeaseNotFoundError: Any
BudgetLeaseStatus: Any
BudgetLeaseStore: Any
ChildLeaseRequest: Any
CostBudgetError: Any
CostObservationConflictError: Any
CostReceiptConflictError: Any
CostReceiptRecord: Any
NativeProcessRecordNotFoundError: Any
PermissionAction: Any
PolicyContext: Any
PolicyDecision: Any
PolicyResolution: Any
RUNTIME_DB_NAME: Any
RuntimeCoordinationStore: Any
evaluate_budget_admission: Any
ActionInboxCommand: Any
ActionInboxConflictError: Any
ActionInboxForbiddenError: Any
ActionInboxItem: Any
ActionInboxKind: Any
ActionInboxNotFoundError: Any
ActionInboxOwnerPort: Any
ActionInboxResponseRequest: Any
ActionInboxResponseResult: Any
ActionInboxService: Any
ActionInboxSnapshot: Any
ActionInboxStatus: Any
ActionInboxValidationError: Any
ActionConsequence: Any
CredentialEgressDenied: Any
CredentialEgressResult: Any
CredentialLeaseDeniedError: Any
CredentialLeaseNotFoundError: Any
CredentialQuotaMetadata: Any
CredentialQuotaStatus: Any
CredentialSourceConflictError: Any
CredentialSourceNotFoundError: Any
CredentialSourceProjection: Any
CredentialSourceRegistration: Any
CredentialSourceScope: Any
CredentialTransportCancelled: Any
CredentialTransportResult: Any
CredentialedTransportPort: Any
GitHubLikeCredentialRequest: Any
HermeticCredentialEgress: Any
InMemoryCredentialBroker: Any
credential_action_scope_digest: Any
credential_source_projection_to_dict: Any

_CREDENTIAL_EXPORTS = (
    "CredentialEgressDenied",
    "CredentialEgressResult",
    "CredentialLeaseDeniedError",
    "CredentialLeaseNotFoundError",
    "CredentialQuotaMetadata",
    "CredentialQuotaStatus",
    "CredentialSourceConflictError",
    "CredentialSourceNotFoundError",
    "CredentialSourceProjection",
    "CredentialSourceRegistration",
    "CredentialSourceScope",
    "CredentialTransportCancelled",
    "CredentialTransportResult",
    "CredentialedTransportPort",
    "GitHubLikeCredentialRequest",
    "HermeticCredentialEgress",
    "InMemoryCredentialBroker",
    "credential_action_scope_digest",
    "credential_source_projection_to_dict",
)

__all__ = [
    "AdmissionDeniedError",
    "ActionConsequence",
    "ActionInboxCommand",
    "ActionInboxConflictError",
    "ActionInboxForbiddenError",
    "ActionInboxItem",
    "ActionInboxKind",
    "ActionInboxNotFoundError",
    "ActionInboxOwnerPort",
    "ActionInboxResponseRequest",
    "ActionInboxResponseResult",
    "ActionInboxService",
    "ActionInboxSnapshot",
    "ActionInboxStatus",
    "ActionInboxValidationError",
    "ApprovalStatus",
    "BudgetHeadroomExceededError",
    "BudgetLeaseBalance",
    "BudgetLeaseClosedError",
    "BudgetLeaseConflictError",
    "BudgetLeaseExpiredError",
    "BudgetLeaseNotFoundError",
    "BudgetLeaseStatus",
    "BudgetLeaseStore",
    "ChildLeaseRequest",
    "CostBudgetError",
    "CostObservationConflictError",
    "CostReceiptConflictError",
    "CostReceiptRecord",
    *_CREDENTIAL_EXPORTS,
    "EnforcementLevel",
    "JobAttemptStatus",
    "NativeProcessOutputRecord",
    "NativeProcessRecord",
    "NativeProcessRecordNotFoundError",
    "PermissionAction",
    "PolicyContext",
    "PolicyDecision",
    "PolicyResolution",
    "RUNTIME_DB_NAME",
    "RunStatus",
    "RuntimeCoordinationStore",
    "evaluate_budget_admission",
    "parse_run_status",
]

_LAZY_EXPORTS = {
    **{name: ("gigaloom.runtime.credentials", name) for name in _CREDENTIAL_EXPORTS},
    "AdmissionDeniedError": (
        "gigaloom.runtime.cost",
        "AdmissionDeniedError",
    ),
    "BudgetHeadroomExceededError": (
        "gigaloom.runtime.cost",
        "BudgetHeadroomExceededError",
    ),
    "BudgetLeaseBalance": (
        "gigaloom.runtime.cost",
        "BudgetLeaseBalance",
    ),
    "BudgetLeaseClosedError": (
        "gigaloom.runtime.cost",
        "BudgetLeaseClosedError",
    ),
    "BudgetLeaseConflictError": (
        "gigaloom.runtime.cost",
        "BudgetLeaseConflictError",
    ),
    "BudgetLeaseExpiredError": (
        "gigaloom.runtime.cost",
        "BudgetLeaseExpiredError",
    ),
    "BudgetLeaseNotFoundError": (
        "gigaloom.runtime.cost",
        "BudgetLeaseNotFoundError",
    ),
    "BudgetLeaseStatus": (
        "gigaloom.runtime.cost",
        "BudgetLeaseStatus",
    ),
    "BudgetLeaseStore": (
        "gigaloom.runtime.cost",
        "BudgetLeaseStore",
    ),
    "ChildLeaseRequest": (
        "gigaloom.runtime.cost",
        "ChildLeaseRequest",
    ),
    "CostBudgetError": (
        "gigaloom.runtime.cost",
        "CostBudgetError",
    ),
    "CostObservationConflictError": (
        "gigaloom.runtime.cost",
        "CostObservationConflictError",
    ),
    "CostReceiptConflictError": (
        "gigaloom.runtime.cost",
        "CostReceiptConflictError",
    ),
    "CostReceiptRecord": (
        "gigaloom.runtime.cost",
        "CostReceiptRecord",
    ),
    "ActionConsequence": (
        "gigaloom.runtime.action_inbox.api",
        "ActionConsequence",
    ),
    "ActionInboxCommand": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxCommand",
    ),
    "ActionInboxConflictError": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxConflictError",
    ),
    "ActionInboxForbiddenError": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxForbiddenError",
    ),
    "ActionInboxItem": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxItem",
    ),
    "ActionInboxKind": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxKind",
    ),
    "ActionInboxNotFoundError": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxNotFoundError",
    ),
    "ActionInboxOwnerPort": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxOwnerPort",
    ),
    "ActionInboxResponseRequest": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxResponseRequest",
    ),
    "ActionInboxResponseResult": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxResponseResult",
    ),
    "ActionInboxService": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxService",
    ),
    "ActionInboxSnapshot": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxSnapshot",
    ),
    "ActionInboxStatus": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxStatus",
    ),
    "ActionInboxValidationError": (
        "gigaloom.runtime.action_inbox.api",
        "ActionInboxValidationError",
    ),
    "EnforcementLevel": (
        "gigaloom.runtime.policy",
        "EnforcementLevel",
    ),
    "NativeProcessRecordNotFoundError": (
        "gigaloom.runtime.store",
        "NativeProcessRecordNotFoundError",
    ),
    "PermissionAction": (
        "gigaloom.runtime.policy",
        "PermissionAction",
    ),
    "PolicyContext": (
        "gigaloom.runtime.policy",
        "PolicyContext",
    ),
    "PolicyDecision": (
        "gigaloom.runtime.policy",
        "PolicyDecision",
    ),
    "PolicyResolution": (
        "gigaloom.runtime.policy",
        "PolicyResolution",
    ),
    "RuntimeCoordinationStore": (
        "gigaloom.runtime.store",
        "RuntimeCoordinationStore",
    ),
    "evaluate_budget_admission": (
        "gigaloom.runtime.cost",
        "evaluate_budget_admission",
    ),
    "RUNTIME_DB_NAME": (
        "gigaloom.runtime.store",
        "RUNTIME_DB_NAME",
    ),
}


def __getattr__(name: str) -> Any:
    """Load policy and store implementations only when requested."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
