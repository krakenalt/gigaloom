"""Presentation readiness projected from persisted managed-agent evidence."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.harnesses.agent_profiles.onboarding.models import ManagedAcpProbeReceipt


@dataclass(frozen=True, slots=True)
class AgentRuntimeReadiness:
    """Versioned provider-bridge readiness shared by CLI and Web."""

    schema_version: int
    status: str
    acp_transport: str
    provider_bridge: str
    protocols: tuple[str, ...]
    gateway_availability: str
    native_launch_available: bool
    reason_ids: tuple[str, ...]
    action: str


def project_agent_runtime_readiness(
    probe: ManagedAcpProbeReceipt,
    *,
    active: bool,
) -> AgentRuntimeReadiness:
    """Map persisted probe evidence to one presentation-only launch status."""
    bridge = probe.provider_bridge
    transport_ready = (
        probe.protocol_state == "conformant"
        and probe.state.value not in {"incompatible", "unsafe", "unavailable"}
    )
    bridge_status = {
        "native_only": "native-only",
        "unknown_until_reprobe": "reprobe",
    }.get(bridge.status, bridge.status)
    reason_ids = bridge.reason_ids
    if not active:
        return AgentRuntimeReadiness(
            schema_version=1,
            status="blocked",
            acp_transport="ready" if transport_ready else "blocked",
            provider_bridge=bridge_status,
            protocols=bridge.protocols,
            gateway_availability="blocked",
            native_launch_available=False,
            reason_ids=tuple(sorted({*reason_ids, "managed_agent_inactive"})),
            action="activate",
        )
    if not transport_ready:
        return AgentRuntimeReadiness(
            schema_version=1,
            status="blocked",
            acp_transport="blocked",
            provider_bridge=bridge_status,
            protocols=bridge.protocols,
            gateway_availability="blocked",
            native_launch_available=False,
            reason_ids=tuple(sorted({*reason_ids, *probe.warnings})),
            action="reprobe",
        )
    if bridge.status == "ready":
        return AgentRuntimeReadiness(
            schema_version=1,
            status="ready",
            acp_transport="ready",
            provider_bridge="ready",
            protocols=bridge.protocols,
            gateway_availability="available",
            native_launch_available=True,
            reason_ids=reason_ids,
            action="select_gateway_route",
        )
    if bridge.status == "native_only":
        return AgentRuntimeReadiness(
            schema_version=1,
            status="native-only",
            acp_transport="ready",
            provider_bridge="native-only",
            protocols=bridge.protocols,
            gateway_availability="unsupported",
            native_launch_available=True,
            reason_ids=reason_ids,
            action="use_native",
        )
    if bridge.status == "unknown_until_reprobe":
        return AgentRuntimeReadiness(
            schema_version=1,
            status="reprobe",
            acp_transport="ready",
            provider_bridge="reprobe",
            protocols=bridge.protocols,
            gateway_availability="reprobe",
            native_launch_available=True,
            reason_ids=reason_ids,
            action="reprobe",
        )
    return AgentRuntimeReadiness(
        schema_version=1,
        status="blocked",
        acp_transport="ready",
        provider_bridge="blocked",
        protocols=bridge.protocols,
        gateway_availability="blocked",
        native_launch_available=True,
        reason_ids=reason_ids,
        action="inspect",
    )
