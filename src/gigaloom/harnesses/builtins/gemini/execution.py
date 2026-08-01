"""Gemini CLI harness for running Gemini through local gpt2giga."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import metadata
import hashlib
from pathlib import Path
import time
from typing import Any, Mapping

from gigaloom.gemini_acp import (
    GeminiAcpError,
)
from gigaloom.execution import (
    EMPTY_EXTENSION_SNAPSHOT_HASH,
    ExecutionClassification,
    ExecutionClassificationStatus,
    ExecutionSnapshot,
    ExecutionTransport,
    InteractionMode,
    ProviderRef,
    RouteRef,
    RuntimeOwnership,
    SnapshotEvidenceRef,
    create_execution_snapshot,
)
from gigaloom.harnesses.ports import ApprovalStatus
from gigaloom.harnesses.ports import (
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyResolution,
)
from gigaloom.managed_mcp import (
    write_startup_config,
)
from gigaloom.types import (
    HarnessContext,
    HarnessEvent,
    HarnessRequest,
)


def _write_gemini_settings(home: Path) -> None:
    write_startup_config(
        "gemini-cli",
        home,
        {"security": {"auth": {"selectedType": "gemini-api-key"}}},
    )


def _structured_owner(request: HarnessRequest) -> str:
    runtime = request.extra.get("runtime")
    worker_id = runtime.get("worker_id") if isinstance(runtime, Mapping) else None
    return str(worker_id or "durable-worker")


def _gemini_execution_snapshot(request: HarnessRequest) -> ExecutionSnapshot:
    api_mode = request.api_mode.value
    model = request.model or "unknown"
    route_revision = hashlib.sha256(f"{api_mode}\0{model}".encode("utf-8")).hexdigest()
    provider = ProviderRef("gigaloom", f"api-{api_mode}")
    workspace_execution = request.extra.get("workspace_execution")
    workspace_execution = (
        workspace_execution if isinstance(workspace_execution, Mapping) else {}
    )
    workspace = str(
        workspace_execution.get("source_workspace") or request.workspace or "unknown"
    )
    effective_workspace = str(request.workspace or workspace)
    workspace_id = (
        "workspace-" + hashlib.sha256(workspace.encode("utf-8")).hexdigest()[:24]
    )
    return create_execution_snapshot(
        provider=provider,
        route=RouteRef(f"route-{route_revision[:24]}", route_revision, provider),
        harness_id="gemini-cli",
        harness_version=_adapter_version(),
        transport=ExecutionTransport.NATIVE_STRUCTURED,
        interaction_mode=InteractionMode.INTERACTIVE,
        runtime_ownership=RuntimeOwnership.DURABLE,
        workspace_id=workspace_id,
        worktree_id=(
            "worktree-"
            + hashlib.sha256(effective_workspace.encode("utf-8")).hexdigest()[:24]
            if effective_workspace != workspace
            else None
        ),
        permission_profile=str(request.mode or "plan"),
        extension_snapshot_hash=EMPTY_EXTENSION_SNAPSHOT_HASH,
        capability_evidence=(
            SnapshotEvidenceRef(
                "gemini-acp",
                "reviewed-v1",
                "supported",
                "gemini-cli-probe",
            ),
        ),
        classification=ExecutionClassification(
            status=ExecutionClassificationStatus.EXPLICIT,
            source="gemini_acp_driver",
        ),
    )


def _durable_approval_bridge(request: HarnessRequest, context: HarnessContext):
    from gigaloom.harnesses.ports import RuntimeCoordinationStore

    if context.data_dir is None:
        raise GeminiAcpError("durable approval requires a Harness data directory")
    store = RuntimeCoordinationStore(context.data_dir)

    def approve(contract: Mapping[str, Any]) -> str:
        binding = str(contract.get("binding_hash") or "")
        if len(binding) != 64:
            raise GeminiAcpError("Gemini ACP approval binding is invalid")
        approval = store.find_approval_request_by_binding(binding)
        if approval is None:
            runtime = request.extra.get("runtime")
            runtime = runtime if isinstance(runtime, Mapping) else {}
            timeout = max(float(context.timeout_seconds), 0.1)
            approval = store.create_approval_request(
                PolicyResolution(
                    action=PermissionAction.MCP_TOOL_CALL,
                    decision=PolicyDecision.ASK,
                    enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
                    policy_source="gemini_acp:request_permission",
                ),
                PolicyContext(
                    session_id=request.session_id,
                    run_id=request.run_id,
                    job_id=str(runtime.get("job_id") or "") or None,
                    reason="Gemini ACP requested permission for a tool call.",
                    preview={
                        "provider": "gemini-acp",
                        "tool_call_id": contract.get("tool_call_id"),
                        "option_kinds": [
                            item.get("kind")
                            for item in contract.get("options", ())
                            if isinstance(item, Mapping)
                        ],
                    },
                    approval_binding=binding,
                    enforcement_owner="gemini_acp.request_permission",
                ),
                expires_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=timeout)
                ).isoformat(),
            )
        if request.event_sink is not None:
            request.event_sink(
                HarnessEvent(
                    type="approval_requested",
                    message="Gemini ACP is waiting for Approval Center.",
                    payload={"approval_id": approval.id},
                )
            )
        deadline = time.monotonic() + max(float(context.timeout_seconds), 0.1)
        while time.monotonic() < deadline:
            current = store.get_approval_request(approval.id)
            if current.status is ApprovalStatus.APPROVED:
                return "allow"
            if current.status in {
                ApprovalStatus.DENIED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELED,
            }:
                return "deny"
            if request.cancel_event is not None and request.cancel_event.is_set():
                return "cancel"
            time.sleep(0.05)
        return "deny"

    return approve


def _managed_mcp_reference(request: HarnessRequest) -> Mapping[str, Any] | None:
    value = request.extra.get("managed_mcp_snapshot")
    return dict(value) if isinstance(value, Mapping) else None


def _adapter_version() -> str:
    try:
        value = metadata.version("gigaloom")
    except metadata.PackageNotFoundError:
        value = "unknown"
    normalized = str(value).strip()
    return normalized if normalized else "unknown"
