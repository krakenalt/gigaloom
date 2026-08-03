"""Public facade for cross-context native operator primitives."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gigaloom.native.launch.gateway_sidecar import (
        DEFAULT_GATEWAY_STARTUP_POLL_SECONDS,
        DEFAULT_GATEWAY_STARTUP_TIMEOUT_SECONDS,
        GatewayArtifactEvidenceV1,
        GatewayProcessLeaseOwner,
        GatewaySidecarReason,
        GatewaySidecarStatus,
        GatewayStartupReadinessProbe,
        ManagedGatewayLeaseV1,
        ManagedGatewaySidecarService,
        UrlLibGatewayStartupReadinessProbe,
    )

from gigaloom.native.launch import (
    AgentResolutionKind,
    AgentResolutionReason,
    AgentResolutionResult,
    BridgeRouteV1,
    DEFAULT_GATEWAY_DISCOVERY_TTL_SECONDS,
    GATEWAY_LAUNCH_SCHEMA_VERSION,
    GatewayMode,
    GatewayAgentInjectionV1,
    GatewayDiscoveryError,
    GatewayDiscoveryReason,
    GatewayDiscoveryResult,
    GatewayDiscoveryStatus,
    GatewayMachineTransport,
    GatewayInjectionReason,
    GatewayInjectionStatus,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewayRouteCatalogV1,
    GatewayRouteDiscovery,
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
    UrlLibGatewayMachineTransport,
    bridge_route_from_dict,
    bridge_route_to_dict,
    build_gateway_agent_injection,
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

_GATEWAY_SIDECAR_EXPORTS = frozenset(
    {
        "DEFAULT_GATEWAY_STARTUP_POLL_SECONDS",
        "DEFAULT_GATEWAY_STARTUP_TIMEOUT_SECONDS",
        "GatewayArtifactEvidenceV1",
        "GatewayProcessLeaseOwner",
        "GatewaySidecarReason",
        "GatewaySidecarStatus",
        "GatewayStartupReadinessProbe",
        "ManagedGatewayLeaseV1",
        "ManagedGatewaySidecarService",
        "UrlLibGatewayStartupReadinessProbe",
    }
)

__all__ = [
    "AgentResolutionKind",
    "AgentResolutionReason",
    "AgentResolutionResult",
    "BridgeRouteV1",
    "CodexStdioJsonRpcClient",
    "DEFAULT_GATEWAY_DISCOVERY_TTL_SECONDS",
    "DEFAULT_GATEWAY_STARTUP_POLL_SECONDS",
    "DEFAULT_GATEWAY_STARTUP_TIMEOUT_SECONDS",
    "GATEWAY_LAUNCH_SCHEMA_VERSION",
    "GatewayMode",
    "GatewayArtifactEvidenceV1",
    "GatewayAgentInjectionV1",
    "GatewayDiscoveryError",
    "GatewayDiscoveryReason",
    "GatewayDiscoveryResult",
    "GatewayDiscoveryStatus",
    "GatewayMachineTransport",
    "GatewayInjectionReason",
    "GatewayInjectionStatus",
    "GatewayPreflightReceiptV1",
    "GatewayPreflightStatus",
    "GatewayProcessLeaseOwner",
    "GatewayProfileV1",
    "GatewayRouteCatalogV1",
    "GatewayRouteDiscovery",
    "GatewaySidecarReason",
    "GatewaySidecarStatus",
    "GatewayStartupReadinessProbe",
    "GatewaySupportStatus",
    "LaunchOverlayV1",
    "ManagedGatewayLeaseV1",
    "ManagedGatewaySidecarService",
    "NativeAgentLaunchSpec",
    "NativeIntentMatcher",
    "NativeIntentMatcherKind",
    "NativeInvocation",
    "NativeLaunchMode",
    "NativeLaunchPlan",
    "NativeLaunchReason",
    "TerminalContext",
    "UrlLibGatewayMachineTransport",
    "UrlLibGatewayStartupReadinessProbe",
    "bridge_route_from_dict",
    "bridge_route_to_dict",
    "build_gateway_agent_injection",
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
    if name in _GATEWAY_SIDECAR_EXPORTS:
        value = getattr(import_module("gigaloom.native.launch.gateway_sidecar"), name)
    elif name == "CodexStdioJsonRpcClient":
        value = getattr(
            import_module("gigaloom.native.codex_operator.protocol"),
            name,
        )
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
