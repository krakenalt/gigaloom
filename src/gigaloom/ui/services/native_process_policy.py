"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from gigaloom.native.models import NativeSessionRef
from gigaloom.runtime.policy import (
    NATIVE_PROCESS_SPAWN_OWNER,
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    approval_request_to_dict,
    permission_profile,
)
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions.models import HarnessSession
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.attachments import (
    session_project_id as _session_project_id,
)
from gigaloom.ui.services.request_values import optional_text as _optional_text


def _native_process_policy_gate(
    *,
    payload: Mapping[str, Any],
    session: HarnessSession,
    policy_engine: PolicyEngine,
    runtime_store: RuntimeCoordinationStore | None,
) -> tuple[str, dict[str, Any]] | JSONResponse:
    """Resolve the Harness-owned spawn action before sidecars or CLIs start."""
    profile = permission_profile(payload.get("permission_profile"), origin="manual")
    run_id = _native_policy_run_id(payload, session.id)
    context = PolicyContext(
        project_id=_session_project_id(session),
        session_id=session.id,
        run_id=run_id,
        reason="Start or resume a managed native CLI process.",
        preview={
            "harness_id": payload.get("harness_id") or session.default_harness_id,
            "action": payload.get("action") or "start",
            "mode": payload.get("mode") or session.default_mode,
            "workspace": payload.get("workspace") or session.workspace,
            "workspace_policy": payload.get("workspace_policy") or "auto",
        },
        enforcement_owner=NATIVE_PROCESS_SPAWN_OWNER,
    )
    resolution = policy_engine.resolve(
        PermissionAction.PROCESS_SPAWN,
        profile=profile,
        context=context,
        enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
    )
    policy_metadata = {
        "action": resolution.action.value,
        "decision": resolution.decision.value,
        "enforcement": resolution.enforcement.value,
        "policy_source": resolution.policy_source,
        "permission_profile": profile.id,
    }
    if resolution.decision is PolicyDecision.DENY:
        raise HTTPException(
            status_code=403, detail="Native process spawn denied by policy"
        )
    if resolution.decision is PolicyDecision.ALLOW:
        return (run_id, policy_metadata)
    if runtime_store is None:
        raise HTTPException(
            status_code=409,
            detail="Durable runtime is required for native process approval",
        )
    approval = runtime_store.create_approval_request(resolution, context)
    return JSONResponse(
        status_code=202,
        content={
            "approval_required": True,
            "approval": approval_request_to_dict(approval),
            "retry": {
                "action": "retry_native_process_start",
                "idempotency_key": payload.get("idempotency_key"),
            },
        },
    )


def _native_policy_run_id(payload: Mapping[str, Any], session_id: str) -> str:
    """Return a stable approval scope without persisting prompt contents."""
    idempotency_key = _optional_text(payload.get("idempotency_key"))
    identity = {
        "session_id": session_id,
        "idempotency_key": idempotency_key,
        "action": payload.get("action") or "start",
        "harness_id": payload.get("harness_id"),
        "native_ref_id": payload.get("native_ref_id"),
        "prompt_sha256": hashlib.sha256(
            str(payload.get("prompt") or "").encode("utf-8")
        ).hexdigest(),
        "workspace": payload.get("workspace"),
        "mode": payload.get("mode"),
        "model": payload.get("model"),
        "api_mode": payload.get("api_mode"),
        "capability": payload.get("capability"),
        "workspace_policy": payload.get("workspace_policy"),
        "attachment_ids": payload.get("attachment_ids"),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"run_native_{digest[:20]}"


def _native_permission_mode(value: Any) -> str:
    mode = str(value or "plan").strip().lower()
    if mode not in {"plan", "read", "edit"}:
        raise ValueError("native mode must be plan, read, or edit")
    return mode


def _native_resume_workspace_execution(options: Mapping[str, Any]) -> dict[str, Any]:
    ref = options.get("native_ref")
    if isinstance(ref, NativeSessionRef):
        plan_metadata = _metadata_mapping(ref.metadata.get("plan_metadata"))
        stored = _metadata_mapping(plan_metadata.get("workspace_execution"))
        if stored:
            return stored
        snapshot = ref.execution_snapshot
        if snapshot is not None and (
            snapshot.source_workspace is not None
            or snapshot.effective_workspace is not None
            or snapshot.workspace_policy is not None
        ):
            effective_workspace = snapshot.effective_workspace or snapshot.workspace
            return {
                "requested_policy": snapshot.workspace_policy or "current",
                "policy": snapshot.workspace_policy or "current",
                "source_workspace": snapshot.source_workspace or snapshot.workspace,
                "source_git_root": snapshot.source_workspace,
                "effective_workspace": effective_workspace,
                "worktree_path": effective_workspace
                if snapshot.workspace_policy == "worktree"
                else None,
                "base_branch": None,
                "base_commit": None,
                "fallback_reason": None,
            }
    workspace = _optional_text(options.get("workspace"))
    return {
        "requested_policy": "current",
        "policy": "current",
        "source_workspace": workspace,
        "source_git_root": None,
        "effective_workspace": workspace,
        "worktree_path": None,
        "base_branch": None,
        "base_commit": None,
        "fallback_reason": "legacy native resume has no stored workspace isolation",
    }
