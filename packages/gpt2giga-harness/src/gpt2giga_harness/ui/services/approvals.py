"""Policy-backed approval gate for reviewed UI actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from gpt2giga_harness.runtime.policy import (
    INTERACTIVE_PROFILE,
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    approval_request_to_dict,
)
from gpt2giga_harness.runtime.store import JobNotFoundError, RuntimeCoordinationStore
from gpt2giga_harness.sessions import HarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessRun, HarnessStoredEvent
from gpt2giga_harness.sessions.store import new_id, utc_now


@dataclass(frozen=True, slots=True)
class ApprovalGateService:
    """Resolve policy and persist one deduplicated approval event."""

    policy_engine: PolicyEngine
    runtime_store: RuntimeCoordinationStore | None
    session_store: HarnessSessionStore

    def gate(
        self,
        action: PermissionAction,
        run: HarnessRun,
        *,
        reason: str,
        preview: Mapping[str, Any],
        approval_binding: str | None = None,
        enforcement_owner: str | None = None,
    ) -> JSONResponse | None:
        """Admit, deny, or request approval for one reviewed action."""
        if self.runtime_store is None:
            raise HTTPException(
                status_code=409,
                detail="Durable runtime is required for policy-gated actions",
            )
        session = self.session_store.get_session(run.session_id)
        runtime_metadata = run.metadata.get("runtime")
        job_id = (
            str(runtime_metadata.get("job_id") or "") or None
            if isinstance(runtime_metadata, Mapping)
            else None
        )
        if job_id is not None:
            try:
                self.runtime_store.get_job(job_id)
            except JobNotFoundError:
                job_id = None
        context = PolicyContext(
            project_id=str(session.metadata.get("project_id") or "") or None,
            session_id=run.session_id,
            run_id=run.id,
            job_id=job_id,
            reason=reason,
            preview=preview,
            approval_binding=approval_binding,
            enforcement_owner=enforcement_owner,
        )
        resolution = self.policy_engine.resolve(
            action,
            profile=INTERACTIVE_PROFILE,
            context=context,
            enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        )
        if resolution.decision is PolicyDecision.DENY:
            raise HTTPException(status_code=403, detail="Action denied by policy")
        if resolution.decision is PolicyDecision.ALLOW:
            return None
        approval = self.runtime_store.create_approval_request(resolution, context)
        existing = any(
            event.type == "approval_requested"
            and event.payload.get("approval_id") == approval.id
            for event in self.session_store.list_events(
                run.session_id,
                run_id=run.id,
            )
        )
        if not existing:
            self.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="approval_requested",
                    message=f"Approval required for {action.value}.",
                    payload={
                        "approval_id": approval.id,
                        "action": action.value,
                        "enforcement": resolution.enforcement.value,
                    },
                    created_at=utc_now(),
                    trace_id=context.job_id or run.id,
                    job_id=context.job_id,
                    span_kind="approval",
                    span_status="pending",
                )
            )
        return JSONResponse(
            status_code=202,
            content={
                "approval_required": True,
                "approval": approval_request_to_dict(approval),
            },
        )
