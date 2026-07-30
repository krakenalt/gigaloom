"""Codex app-server execution snapshots."""

from __future__ import annotations

from typing import Any, Mapping

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
from gigaloom.types import (
    HarnessRequest,
)

from gigaloom.harnesses.builtins.codex.app_server.utils import (
    _identity_value,
    _json_hash,
    _mapping,
)


def build_execution_snapshot(
    request: HarnessRequest,
    *,
    managed_home_id: str,
) -> dict[str, Any]:
    """Build the immutable public identity required for continued turns."""
    managed_mcp = _mapping(request.extra.get("managed_mcp_snapshot"))
    workspace_execution = _mapping(request.extra.get("workspace_execution"))
    content = {
        "schema_version": 1,
        "harness_id": "codex-cli",
        "api_mode": request.api_mode.value,
        "model": request.model,
        "workspace": request.workspace,
        "source_workspace": workspace_execution.get("source_workspace"),
        "permission_mode": request.mode,
        "managed_home_id": managed_home_id,
        "tool_snapshot_id": managed_mcp.get("snapshot_id"),
        "tool_snapshot_hash": managed_mcp.get("snapshot_hash"),
    }
    return {**content, "snapshot_hash": _json_hash(content)}


def build_structured_execution_snapshot(
    legacy_snapshot: Mapping[str, Any],
    *,
    adapter_version: str,
    cli_version: str | None = None,
) -> ExecutionSnapshot:
    """Project one verified Codex continuity snapshot into the neutral contract."""
    snapshot = dict(legacy_snapshot)
    supplied_hash = str(snapshot.pop("snapshot_hash", ""))
    if supplied_hash != _json_hash(snapshot):
        raise ValueError("Codex app-server execution snapshot hash mismatch")
    if snapshot.get("harness_id") != "codex-cli":
        raise ValueError("Codex structured snapshot has the wrong adapter identity")
    api_mode = str(snapshot.get("api_mode") or "unknown")
    model = str(snapshot.get("model") or "unknown")
    route_revision = _json_hash({"api_mode": api_mode, "model": model})
    provider = ProviderRef(
        "gigaloom",
        _identity_value(f"api-{api_mode}", prefix="provider"),
    )
    route = RouteRef(
        f"route-{route_revision[:24]}",
        route_revision,
        provider,
    )
    source_workspace = str(
        snapshot.get("source_workspace") or snapshot.get("workspace") or "unknown"
    )
    effective_workspace = str(snapshot.get("workspace") or source_workspace)
    workspace_id = f"workspace-{_json_hash({'path': source_workspace})[:24]}"
    worktree_id = (
        f"worktree-{_json_hash({'path': effective_workspace})[:24]}"
        if effective_workspace != source_workspace
        else None
    )
    extension_hash = str(snapshot.get("tool_snapshot_hash") or "")
    if len(extension_hash) != 64:
        extension_hash = EMPTY_EXTENSION_SNAPSHOT_HASH
    capability_evidence = (
        SnapshotEvidenceRef(
            "codex-app-server",
            cli_version or adapter_version,
            "supported",
            "codex-cli-probe",
        ),
    )
    return create_execution_snapshot(
        provider=provider,
        route=route,
        harness_id="codex-cli",
        harness_version=adapter_version,
        transport=ExecutionTransport.NATIVE_STRUCTURED,
        interaction_mode=InteractionMode.INTERACTIVE,
        runtime_ownership=RuntimeOwnership.DURABLE,
        workspace_id=workspace_id,
        worktree_id=worktree_id,
        permission_profile=_identity_value(
            snapshot.get("permission_mode"),
            prefix="permission",
        ),
        extension_snapshot_hash=extension_hash,
        capability_evidence=capability_evidence,
        classification=ExecutionClassification(
            status=ExecutionClassificationStatus.EXPLICIT,
            source="codex_app_server_driver",
            evidence=(f"snapshot-{supplied_hash[:24]}",),
        ),
    )
