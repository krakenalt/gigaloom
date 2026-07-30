"""Policy and approval coordination for pull-request creation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gigaloom.runtime import policy as runtime_policy
from gigaloom.runtime import store as runtime_store

from .pull_request_contracts import (
    ENVIRONMENT_PULL_REQUEST_OWNER,
    EnvironmentPullRequestError,
    EnvironmentPullRequestPreview,
    EnvironmentPullRequestResult,
)

EnforcementLevel = runtime_policy.EnforcementLevel
INTERACTIVE_PROFILE = runtime_policy.INTERACTIVE_PROFILE
PermissionAction = runtime_policy.PermissionAction
PolicyContext = runtime_policy.PolicyContext
PolicyDecision = runtime_policy.PolicyDecision
PolicyEngine = runtime_policy.PolicyEngine
RuntimeCoordinationStore = runtime_store.RuntimeCoordinationStore


@dataclass(frozen=True)
class EnvironmentPullRequestOutcome:
    """Policy-aware PR outcome returned to presentation clients."""

    preview: EnvironmentPullRequestPreview
    approval: Any | None = None
    result: EnvironmentPullRequestResult | None = None
    idempotent_replay: bool = False


class GovernedEnvironmentPullRequestService:
    """Keep policy, approval, stale checks, and hosted mutation under one owner."""

    def __init__(
        self,
        pull_request_service: Any,
        runtime_store: RuntimeCoordinationStore,
        policy_engine: PolicyEngine,
    ) -> None:
        self.pull_request_service = pull_request_service
        self.runtime_store = runtime_store
        self.policy_engine = policy_engine

    def apply_or_request(
        self,
        preview_id: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> EnvironmentPullRequestOutcome:
        """Apply a matching grant or create one exact allow-once approval."""
        preview = self.pull_request_service.get_preview(preview_id)
        completed = self.pull_request_service.completed_result(preview.id)
        if completed is not None:
            return EnvironmentPullRequestOutcome(
                preview=preview, result=completed, idempotent_replay=True
            )
        self.pull_request_service.validate_current(preview)
        context = PolicyContext(
            project_id=project_id or preview.scope_id,
            session_id=session_id,
            reason="Create the exact reviewed pull request in the exact repository.",
            preview=preview.to_dict(),
            approval_binding=preview.approval_binding,
            enforcement_owner=ENVIRONMENT_PULL_REQUEST_OWNER,
        )
        resolution = self.policy_engine.resolve(
            PermissionAction.GITHUB_PULL_REQUEST_CREATE,
            profile=INTERACTIVE_PROFILE,
            context=context,
            enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        )
        if resolution.decision is PolicyDecision.DENY:
            raise EnvironmentPullRequestError(
                "policy_denied", "Pull-request creation denied by policy."
            )
        if resolution.decision is PolicyDecision.ASK:
            approval = self.runtime_store.create_approval_request(resolution, context)
            return EnvironmentPullRequestOutcome(preview=preview, approval=approval)
        result = self.pull_request_service.apply(preview.id)
        return EnvironmentPullRequestOutcome(preview=preview, result=result)
