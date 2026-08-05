"""Fail-closed ACP permission bridge bound to product-owned authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from typing import TYPE_CHECKING, Literal

from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    RequestPermissionRequest,
    RequestPermissionResponse,
)

from gigaloom.harnesses.acp.errors import AcpPermissionError, AcpProtocolError
from gigaloom.harnesses.acp.sessions import AcpSessionBindingV1, require_session
from gigaloom.structured_processes import StructuredBridgeKind, StructuredBridgeRequest

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


ActionClass = Literal[
    "filesystem_read",
    "filesystem_write",
    "terminal",
    "network",
    "reasoning",
    "configuration",
    "unknown",
]

_ACTION_BY_TOOL_KIND: dict[str, ActionClass] = {
    "read": "filesystem_read",
    "search": "filesystem_read",
    "edit": "filesystem_write",
    "delete": "filesystem_write",
    "move": "filesystem_write",
    "execute": "terminal",
    "fetch": "network",
    "think": "reasoning",
    "switch_mode": "configuration",
    "other": "unknown",
}


@dataclass(frozen=True, slots=True)
class AcpPermissionContextV1:
    """Parent-owned authority ceiling and revision binding for one run."""

    agent_id: str
    route_id: str
    run_id: str
    session_id: str
    workspace_digest: str
    policy_revision: str
    expires_at: datetime
    allowed_action_classes: frozenset[ActionClass]

    def __post_init__(self) -> None:
        if self.expires_at.tzinfo is None:
            raise ValueError("ACP permission expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class AcpPermissionOptionV1:
    """Content-free provider option identity and semantic kind."""

    option_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class AcpPermissionRequestV1:
    """Content-free, generation-bound permission request projection."""

    provider_request_id: str | int
    generation: int
    session_id: str
    tool_call_id: str
    action_class: ActionClass
    options: tuple[AcpPermissionOptionV1, ...]
    admissible: bool
    binding_digest: str


def next_permission(
    client: AcpClient,
    binding: AcpSessionBindingV1,
    context: AcpPermissionContextV1,
    *,
    timeout: float,
) -> AcpPermissionRequestV1 | None:
    """Return the next live request after validating all parent bindings."""
    require_session(client, binding)
    _validate_context(client, binding, context)
    bridge = client.supervisor.next_bridge_request(timeout=timeout)
    if bridge is None:
        return None
    if bridge.kind is not StructuredBridgeKind.APPROVAL:
        raise AcpPermissionError("ACP bridge request is not a permission request")
    request = _validate_request(bridge)
    if request.session_id != binding.acp_session_id:
        raise AcpPermissionError("ACP permission targets the wrong session")
    action = _ACTION_BY_TOOL_KIND.get(request.tool_call.kind or "", "unknown")
    options = tuple(
        AcpPermissionOptionV1(item.option_id, item.kind) for item in request.options
    )
    projection = AcpPermissionRequestV1(
        provider_request_id=bridge.id,
        generation=bridge.generation,
        session_id=request.session_id,
        tool_call_id=request.tool_call.tool_call_id,
        action_class=action,
        options=options,
        admissible=action in context.allowed_action_classes,
        binding_digest="",
    )
    return replace(
        projection, binding_digest=_binding_digest(binding, context, projection)
    )


def respond_permission(
    client: AcpClient,
    binding: AcpSessionBindingV1,
    context: AcpPermissionContextV1,
    request: AcpPermissionRequestV1,
    *,
    allow: bool,
    option_id: str | None = None,
) -> None:
    """Respond once without allowing provider prose to expand authority."""
    require_session(client, binding)
    _validate_context(client, binding, context)
    if request.binding_digest != _binding_digest(binding, context, request):
        raise AcpPermissionError("ACP permission binding changed")
    selected = _select_option(request, allow=allow, option_id=option_id)
    if allow and (
        not request.admissible
        or request.action_class not in context.allowed_action_classes
    ):
        raise AcpPermissionError("ACP permission exceeds the parent authority ceiling")
    outcome = (
        AllowedOutcome(option_id=selected.option_id, outcome="selected")
        if selected is not None
        else DeniedOutcome(outcome="cancelled")
    )
    response = RequestPermissionResponse(outcome=outcome)
    client.supervisor.respond_bridge(
        request.provider_request_id,
        generation=request.generation,
        result=response.model_dump(mode="json", by_alias=True, exclude_none=True),
    )


def _select_option(
    request: AcpPermissionRequestV1, *, allow: bool, option_id: str | None
) -> AcpPermissionOptionV1 | None:
    permitted = (
        {"allow_once", "allow_always"} if allow else {"reject_once", "reject_always"}
    )
    candidates = [item for item in request.options if item.kind in permitted]
    if option_id is not None:
        candidates = [item for item in candidates if item.option_id == option_id]
    if allow and len(candidates) != 1:
        raise AcpPermissionError("ACP allow decision requires one matching option")
    return candidates[0] if candidates else None


def _validate_context(
    client: AcpClient,
    binding: AcpSessionBindingV1,
    context: AcpPermissionContextV1,
) -> None:
    if datetime.now(UTC) >= context.expires_at.astimezone(UTC):
        raise AcpPermissionError("ACP permission context expired")
    if (
        context.agent_id != client.route_identity.agent_id
        or context.route_id != client.route_identity.route_id
        or context.session_id != binding.gigaloom_session_id
        or context.workspace_digest != binding.workspace_digest
    ):
        raise AcpPermissionError(
            "ACP permission context does not match the route binding"
        )


def _binding_digest(
    binding: AcpSessionBindingV1,
    context: AcpPermissionContextV1,
    request: AcpPermissionRequestV1,
) -> str:
    payload = {
        "agent_id": context.agent_id,
        "route_id": context.route_id,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "acp_session_id": binding.acp_session_id,
        "workspace_digest": context.workspace_digest,
        "policy_revision": context.policy_revision,
        "expires_at": context.expires_at.astimezone(UTC).isoformat(),
        "generation": request.generation,
        "provider_request_id": request.provider_request_id,
        "provider_session_id": request.session_id,
        "tool_call_id": request.tool_call_id,
        "action_class": request.action_class,
        "options": [
            {"option_id": item.option_id, "kind": item.kind} for item in request.options
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate_request(bridge: StructuredBridgeRequest) -> RequestPermissionRequest:
    try:
        return RequestPermissionRequest.model_validate(bridge.params)
    except Exception as exc:
        raise AcpProtocolError(
            "ACP permission request failed schema validation"
        ) from exc
