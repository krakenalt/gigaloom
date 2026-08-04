"""Agent-specific overlays for one reviewed gateway route."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.native.launch.gateway_contracts import (
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewaySupportStatus,
    LaunchOverlayV1,
    ResolvedGatewayRoute,
)


class GatewayInjectionStatus(str, Enum):
    """Agent overlay planning outcome."""

    READY = "ready"
    ACKNOWLEDGEMENT_REQUIRED = "acknowledgement_required"
    BLOCKED = "blocked"


class GatewayInjectionReason(str, Enum):
    """Content-free adapter and revision refusal reasons."""

    CAPABILITY_UNKNOWN = "capability_unknown"
    CAPABILITY_STALE = "capability_stale"
    ROUTE_NOT_CURRENT = "route_not_current"
    PROFILE_BINDING_MISMATCH = "profile_binding_mismatch"
    PREFLIGHT_BINDING_MISMATCH = "preflight_binding_mismatch"
    PROCESS_LEASE_REQUIRED = "process_lease_required"
    SUPPORT_BLOCKED = "support_blocked"
    ACKNOWLEDGEMENT_REQUIRED = "acknowledgement_required"
    AGENT_PROTOCOL_UNSUPPORTED = "agent_protocol_unsupported"
    GEMINI_NATIVE_GATEWAY_UNSUPPORTED = "gemini_custom_endpoint_unsupported"
    MANAGED_ROOT_INVALID = "managed_root_invalid"
    OVERLAY_WRITE_FAILED = "overlay_write_failed"


@dataclass(frozen=True)
class GatewayAgentInjectionV1:
    """Generated overlay or typed refusal for one agent adapter."""

    status: GatewayInjectionStatus
    route_id: str
    agent_id: str
    effective_support_status: GatewaySupportStatus
    overlay: LaunchOverlayV1 | None
    command_args: tuple[str, ...]
    reason_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return whether this adapter-specific overlay may be used."""
        return self.status is GatewayInjectionStatus.READY


def build_gateway_agent_injection(
    route: ResolvedGatewayRoute,
    agent_id: str,
    profile: GatewayProfileV1,
    preflight: GatewayPreflightReceiptV1,
    *,
    managed_root: str | os.PathLike[str],
    process_lease_ref: str | None,
    acknowledged: bool = False,
) -> GatewayAgentInjectionV1:
    """Build a route-bound overlay without mutating provider-native homes."""
    support_status = GatewaySupportStatus(route.support_status)
    binding_refusal = _binding_refusal(route, profile, preflight)
    if binding_refusal is not None:
        return _blocked(route, agent_id, support_status, binding_refusal)
    if profile.mode is GatewayMode.MANAGED and process_lease_ref is None:
        return _blocked(
            route,
            agent_id,
            support_status,
            GatewayInjectionReason.PROCESS_LEASE_REQUIRED,
        )
    if support_status is GatewaySupportStatus.BLOCKED:
        return _blocked(
            route,
            agent_id,
            GatewaySupportStatus.BLOCKED,
            GatewayInjectionReason.SUPPORT_BLOCKED,
        )
    adapter = _adapter_kind(agent_id, route.provider_protocol)
    if adapter == "gemini":
        return _blocked(
            route,
            agent_id,
            GatewaySupportStatus.BLOCKED,
            GatewayInjectionReason.GEMINI_NATIVE_GATEWAY_UNSUPPORTED,
        )
    if adapter is None:
        return _blocked(
            route,
            agent_id,
            GatewaySupportStatus.BLOCKED,
            GatewayInjectionReason.AGENT_PROTOCOL_UNSUPPORTED,
        )
    effective_support = (
        GatewaySupportStatus.VENDOR_UNSUPPORTED
        if adapter == "claude"
        else support_status
    )
    acknowledgement_required = (
        effective_support is GatewaySupportStatus.VENDOR_UNSUPPORTED
    )
    if acknowledgement_required and not acknowledged:
        return GatewayAgentInjectionV1(
            status=GatewayInjectionStatus.ACKNOWLEDGEMENT_REQUIRED,
            route_id=route.route_id,
            agent_id=agent_id,
            effective_support_status=effective_support,
            overlay=None,
            command_args=(),
            reason_ids=(GatewayInjectionReason.ACKNOWLEDGEMENT_REQUIRED.value,),
        )
    root = Path(managed_root)
    if not root.is_absolute() or _native_home_component(root):
        return _blocked(
            route,
            agent_id,
            effective_support,
            GatewayInjectionReason.MANAGED_ROOT_INVALID,
        )
    overlay_root = root / "agent-overlays" / _slug(route.route_id)
    try:
        overlay_root.mkdir(parents=True, exist_ok=True)
        overlay_root.chmod(0o700)
        generated_refs, environment, command_args = _write_adapter_overlay(
            adapter,
            route,
            profile,
            overlay_root,
        )
    except OSError:
        return _blocked(
            route,
            agent_id,
            effective_support,
            GatewayInjectionReason.OVERLAY_WRITE_FAILED,
        )
    overlay = LaunchOverlayV1(
        route_id=route.route_id,
        managed_home=os.fspath(overlay_root),
        redacted_env_delta=environment,
        generated_config_refs=generated_refs,
        process_lease_ref=process_lease_ref,
        preflight_receipt_ref=f"gateway-preflight:{preflight.receipt_id}",
        gateway_capability_digest=route.capability_digest,
    )
    reasons = route.reason_ids
    if effective_support is GatewaySupportStatus.VENDOR_UNSUPPORTED:
        reasons = (*reasons, "agent_vendor_support_absent")
    return GatewayAgentInjectionV1(
        status=GatewayInjectionStatus.READY,
        route_id=route.route_id,
        agent_id=agent_id,
        effective_support_status=effective_support,
        overlay=overlay,
        command_args=command_args,
        reason_ids=tuple(dict.fromkeys(reasons)),
    )


def _binding_refusal(
    route: ResolvedGatewayRoute,
    profile: GatewayProfileV1,
    receipt: GatewayPreflightReceiptV1,
) -> GatewayInjectionReason | None:
    if route.gateway_id != profile.gateway_id:
        return GatewayInjectionReason.PROFILE_BINDING_MISMATCH
    receipt_digest = canonical_digest(
        {
            "capability_revision": receipt.capability_revision,
            "models_revision": receipt.models_revision,
            "loss_matrix_revision": receipt.loss_matrix_revision,
        }
    )
    if (
        receipt.status is not GatewayPreflightStatus.READY
        or receipt.gateway_id != profile.gateway_id
        or receipt.route_id != route.route_id
        or receipt.profile_digest != profile.profile_digest
        or receipt_digest != route.capability_digest
        or receipt.support_status.value != route.support_status
    ):
        return GatewayInjectionReason.PREFLIGHT_BINDING_MISMATCH
    return None


def _adapter_kind(agent_id: str, provider_protocol: str) -> str | None:
    if agent_id == "codex" and provider_protocol == "openai_responses":
        return "codex"
    if agent_id == "claude" and provider_protocol == "anthropic_messages":
        return "claude"
    if agent_id == "gemini":
        return "gemini"
    return None


def _write_adapter_overlay(
    adapter: str,
    route: ResolvedGatewayRoute,
    profile: GatewayProfileV1,
    overlay_root: Path,
) -> tuple[
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    tuple[str, ...],
]:
    if adapter == "codex":
        config = overlay_root / "config.toml"
        config.write_text(_codex_config(route, profile), encoding="utf-8")
        config.chmod(0o600)
        return (
            ("managed-config:config.toml",),
            (
                ("CODEX_HOME", os.fspath(overlay_root)),
                ("GPT2GIGA_API_KEY", "<secret-ref>"),
            ),
            (),
        )
    if adapter == "claude":
        return (
            (),
            (
                ("ANTHROPIC_AUTH_TOKEN", "<secret-ref>"),
                ("ANTHROPIC_BASE_URL", _api_base_url(profile, "v1")),
                ("CLAUDE_CONFIG_DIR", os.fspath(overlay_root)),
            ),
            ("--model", route.public_model_alias),
        )
    raise AssertionError("unsupported gateway adapter")


def _codex_config(route: ResolvedGatewayRoute, profile: GatewayProfileV1) -> str:
    provider = "gigaloom-gateway"
    return (
        f'model = "{_toml_escape(route.public_model_alias)}"\n'
        f'model_provider = "{provider}"\n\n'
        f"[model_providers.{provider}]\n"
        f'name = "{provider}"\n'
        f'base_url = "{_toml_escape(_api_base_url(profile, "v2"))}"\n'
        'env_key = "GPT2GIGA_API_KEY"\n'
        'wire_api = "responses"\n'
        "supports_websockets = false\n"
    )


def _api_base_url(profile: GatewayProfileV1, version: str) -> str:
    return f"{profile.base_url.rstrip('/')}/{version}"


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _native_home_component(path: Path) -> bool:
    return bool({".codex", ".claude", ".gemini"}.intersection(path.parts))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _blocked(
    route: ResolvedGatewayRoute,
    agent_id: str,
    support: GatewaySupportStatus,
    reason: GatewayInjectionReason,
) -> GatewayAgentInjectionV1:
    return GatewayAgentInjectionV1(
        status=GatewayInjectionStatus.BLOCKED,
        route_id=route.route_id,
        agent_id=agent_id,
        effective_support_status=support,
        overlay=None,
        command_args=(),
        reason_ids=(reason.value,),
    )
