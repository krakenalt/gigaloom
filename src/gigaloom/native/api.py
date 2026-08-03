"""Public facade for cross-context native operator primitives."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from gigaloom.native.launch import (
    AgentResolutionKind,
    AgentResolutionReason,
    AgentResolutionResult,
    BridgeRouteV1,
    GATEWAY_LAUNCH_SCHEMA_VERSION,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewaySupportStatus,
    LaunchOverlayV1,
    NativeAgentLaunchSpec,
    NativeIntentMatcher,
    NativeIntentMatcherKind,
    NativeInvocation,
    NativeLaunchMode,
    NativeLaunchPlan,
    NativeLaunchReason,
    TerminalContext,
    bridge_route_from_dict,
    bridge_route_to_dict,
    gateway_contract_digest,
    gateway_preflight_receipt_from_dict,
    gateway_preflight_receipt_to_dict,
    gateway_profile_from_dict,
    gateway_profile_to_dict,
    launch_overlay_from_dict,
    launch_overlay_to_dict,
    plan_native_launch,
)

CodexStdioJsonRpcClient: Any

__all__ = [
    "AgentResolutionKind",
    "AgentResolutionReason",
    "AgentResolutionResult",
    "BridgeRouteV1",
    "CodexStdioJsonRpcClient",
    "GATEWAY_LAUNCH_SCHEMA_VERSION",
    "GatewayMode",
    "GatewayPreflightReceiptV1",
    "GatewayPreflightStatus",
    "GatewayProfileV1",
    "GatewaySupportStatus",
    "LaunchOverlayV1",
    "NativeAgentLaunchSpec",
    "NativeIntentMatcher",
    "NativeIntentMatcherKind",
    "NativeInvocation",
    "NativeLaunchMode",
    "NativeLaunchPlan",
    "NativeLaunchReason",
    "TerminalContext",
    "bridge_route_from_dict",
    "bridge_route_to_dict",
    "gateway_contract_digest",
    "gateway_preflight_receipt_from_dict",
    "gateway_preflight_receipt_to_dict",
    "gateway_profile_from_dict",
    "gateway_profile_to_dict",
    "launch_overlay_from_dict",
    "launch_overlay_to_dict",
    "plan_native_launch",
]


def __getattr__(name: str) -> Any:
    """Resolve compatibility-stable operator clients lazily."""
    if name != "CodexStdioJsonRpcClient":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(
        import_module("gigaloom.native.codex_operator.protocol"),
        name,
    )
    globals()[name] = value
    return value
