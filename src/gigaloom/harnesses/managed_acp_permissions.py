"""Permission projection and responses for managed ACP turns."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.acp import AcpPermissionContextV1, respond_permission
from gigaloom.harnesses.acp.errors import AcpPermissionError
from gigaloom.runtime.api import PermissionAction, PolicyDecision, permission_profile


class ManagedAcpAuthenticationRequired(RuntimeError):
    """Raised when a managed connector requires provider-owned authentication."""


class ManagedAcpPermissionTurn(Protocol):
    """Minimal turn authority used to construct an ACP permission context."""

    agent_id: str
    network_profile: str
    permission_profile_id: str
    route_id: str
    run_id: str
    timeout_seconds: float


def is_canceled(value: object | None) -> bool:
    """Return whether an optional cancellation token is set."""
    checker = getattr(value, "is_set", None)
    return bool(checker()) if callable(checker) else False


def permission_context(
    request: ManagedAcpPermissionTurn,
    binding,  # noqa: ANN001
) -> AcpPermissionContextV1:
    """Project one product permission profile into bounded ACP authority."""
    selected = permission_profile(request.permission_profile_id, origin="interactive")
    action_map = {
        "filesystem_read": PermissionAction.WORKSPACE_READ,
        "filesystem_write": PermissionAction.WORKSPACE_WRITE,
        "terminal": PermissionAction.PROCESS_SPAWN,
        "network": PermissionAction.NETWORK_CONNECT,
    }
    allowed = {"reasoning"} | {
        action_class
        for action_class, action in action_map.items()
        if selected.decision_for(action) is PolicyDecision.ALLOW
    }
    revision = canonical_digest(
        {
            "permission_profile": selected.id,
            "network_profile": request.network_profile,
            "allowed_action_classes": sorted(allowed),
        }
    )
    return AcpPermissionContextV1(
        agent_id=request.agent_id,
        route_id=request.route_id,
        run_id=request.run_id,
        session_id=binding.gigaloom_session_id,
        workspace_digest=binding.workspace_digest,
        policy_revision=revision,
        expires_at=datetime.now(UTC) + timedelta(seconds=request.timeout_seconds),
        allowed_action_classes=frozenset(allowed),
    )


def answer_permission(client, binding, context, pending) -> None:  # noqa: ANN001
    """Answer one admissible allow-once request or deny it fail-closed."""
    if not pending.admissible:
        respond_permission(client, binding, context, pending, allow=False)
        raise AcpPermissionError("managed ACP permission exceeds parent authority")
    option = next(
        (item.option_id for item in pending.options if item.kind == "allow_once"),
        None,
    )
    respond_permission(
        client,
        binding,
        context,
        pending,
        allow=True,
        option_id=option,
    )


__all__ = """ManagedAcpAuthenticationRequired answer_permission is_canceled
permission_context""".split()
