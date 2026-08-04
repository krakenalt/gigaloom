"""Fail-closed gpt2giga launch inputs for managed ACP agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from gigaloom.types import GigaChatApiMode, HarnessContext


class ManagedAcpGatewayTurn(Protocol):
    """Minimal transient turn facts required by the gateway overlay."""

    gateway_api_key: str | None
    gateway_base_url: str | None
    gateway_profile_id: str | None
    gateway_route_id: str | None
    model_id: str


class ManagedAcpGatewaySupport(str, Enum):
    """Reviewed support state for one managed ACP provider overlay."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ManagedAcpGatewayInjection(str, Enum):
    """Exact provider-configuration mechanism used by one ACP agent."""

    OPENCODE_CONFIG_CONTENT_V1 = "opencode_config_content_v1"


@dataclass(frozen=True, slots=True)
class ManagedAcpGatewayCapabilityV1:
    """Separate ACP transport support from the agent's model-provider protocol."""

    registry_id: str
    support: ManagedAcpGatewaySupport
    provider_protocol: str | None
    injection: ManagedAcpGatewayInjection | None
    reason_id: str
    session_model_config_id: str | None = None
    schema_version: int = 1

    def projection(self) -> dict[str, object]:
        """Return a content-free UI projection for this exact adapter."""
        return {
            "schema_version": self.schema_version,
            "support": self.support.value,
            "provider_protocol": self.provider_protocol,
            "injection": self.injection.value if self.injection is not None else None,
            "session_model_config_id": self.session_model_config_id,
            "reason_id": self.reason_id,
        }


class ManagedAcpGatewayUnsupported(ValueError):
    """Raised when no reviewed provider overlay exists for an ACP agent."""

    def __init__(self, reason_id: str) -> None:
        super().__init__(reason_id)
        self.reason_id = reason_id


class ManagedAcpGatewayRouteRequired(ValueError):
    """Raised when a selected gateway model lacks an exact reviewed binding."""


@dataclass(frozen=True, slots=True)
class GatewayRouteSelection:
    """Reviewed route values safe to retain for one transient ACP turn."""

    route_id: str
    gateway_profile_id: str
    public_model_alias: str
    model_id: str
    base_url: str
    session_model_config_id: str | None
    api_key: str | None = field(default=None, repr=False)


_OPENCODE_CAPABILITY = ManagedAcpGatewayCapabilityV1(
    registry_id="opencode",
    support=ManagedAcpGatewaySupport.SUPPORTED,
    provider_protocol="openai_chat_completions",
    injection=ManagedAcpGatewayInjection.OPENCODE_CONFIG_CONTENT_V1,
    reason_id="opencode_config_content_overlay_reviewed",
)


def managed_acp_gateway_capability(
    registry_id: str,
) -> ManagedAcpGatewayCapabilityV1:
    """Return reviewed provider-routing support without inferring it from ACP."""
    if registry_id == "opencode":
        return _OPENCODE_CAPABILITY
    if registry_id == "amp-acp":
        return ManagedAcpGatewayCapabilityV1(
            registry_id=registry_id,
            support=ManagedAcpGatewaySupport.UNSUPPORTED,
            provider_protocol=None,
            injection=None,
            reason_id="amp_acp_provider_configuration_unsupported",
        )
    return ManagedAcpGatewayCapabilityV1(
        registry_id=registry_id,
        support=ManagedAcpGatewaySupport.UNKNOWN,
        provider_protocol=None,
        injection=None,
        reason_id="acp_provider_configuration_not_advertised",
    )


def gateway_route_selection(
    value: object,
    context: HarnessContext,
    *,
    registry_id: str,
) -> GatewayRouteSelection | None:
    """Decode one validated ACP binding without falling back on malformed data."""
    if value is None:
        return None
    capability = managed_acp_gateway_capability(registry_id)
    if capability.support is not ManagedAcpGatewaySupport.SUPPORTED:
        raise ManagedAcpGatewayUnsupported(capability.reason_id)
    if not isinstance(value, Mapping):
        raise ValueError("managed ACP gateway route binding is invalid")
    if value.get("schema_version") != 1 or value.get("agent_id") != "acp":
        raise ValueError("managed ACP gateway route binding is incompatible")
    gateway_profile_id = _route_text(
        value.get("gateway_profile_id"),
        "gateway profile id",
    )
    if gateway_profile_id != "gpt2giga":
        raise ValueError("managed ACP gateway profile is unsupported")
    support_status = _route_text(value.get("support_status"), "support status")
    if support_status == "blocked":
        raise ValueError("managed ACP gateway route is blocked")
    if support_status not in {
        "stable",
        "technical_preview",
        "vendor_unsupported",
    }:
        raise ValueError("managed ACP gateway support status is invalid")
    route_id = _route_text(value.get("route_id"), "gateway route id")
    public_model_alias = _route_text(
        value.get("public_model_alias"),
        "gateway model alias",
    )
    model_id = f"{gateway_profile_id}/{public_model_alias}"
    return GatewayRouteSelection(
        route_id=route_id,
        gateway_profile_id=gateway_profile_id,
        public_model_alias=public_model_alias,
        model_id=model_id,
        base_url=context.api_base_url(GigaChatApiMode.V1),
        session_model_config_id=capability.session_model_config_id,
        api_key=context.api_key,
    )


def require_managed_acp_gateway_route(
    *,
    registry_id: str,
    requested_model: str | None,
    selection: GatewayRouteSelection | None,
) -> None:
    """Reject a selected gateway model before an ACP provider-default launch."""
    model = (requested_model or "").strip()
    if not model or model == "provider-default" or selection is not None:
        return
    capability = managed_acp_gateway_capability(registry_id)
    if capability.support is not ManagedAcpGatewaySupport.SUPPORTED:
        raise ManagedAcpGatewayUnsupported(capability.reason_id)
    raise ManagedAcpGatewayRouteRequired(
        "managed ACP gateway model requires a reviewed route binding"
    )


def gateway_process_environment(
    request: ManagedAcpGatewayTurn,
    *,
    registry_id: str,
    root: Path,
) -> dict[str, str]:
    """Build an ephemeral OpenAI-compatible environment and OpenCode config."""
    del root
    if request.gateway_base_url is None:
        return {}
    if (
        request.gateway_route_id is None
        or request.gateway_profile_id != "gpt2giga"
        or request.model_id == "provider-default"
    ):
        raise ValueError("managed ACP gateway launch inputs are incomplete")
    base_url = _gateway_url(request.gateway_base_url)
    capability = managed_acp_gateway_capability(registry_id)
    if capability.support is not ManagedAcpGatewaySupport.SUPPORTED:
        raise ManagedAcpGatewayUnsupported(capability.reason_id)
    api_key = request.gateway_api_key or "0"
    environment = {
        "GPT2GIGA_API_KEY": api_key,
        "GPT2GIGA_BASE_URL": base_url,
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": base_url,
    }
    if (
        capability.injection
        is not ManagedAcpGatewayInjection.OPENCODE_CONFIG_CONTENT_V1
    ):
        raise ManagedAcpGatewayUnsupported("acp_gateway_injection_not_implemented")
    model_alias = request.model_id.removeprefix("gpt2giga/")
    if not model_alias or model_alias == request.model_id:
        raise ValueError("OpenCode gateway model selector is invalid")
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "model": request.model_id,
            "provider": {
                "gpt2giga": {
                    "name": "gpt2giga",
                    "npm": "@ai-sdk/openai-compatible",
                    "models": {model_alias: {"name": model_alias}},
                    "options": {
                        "apiKey": "{env:GPT2GIGA_API_KEY}",
                        "baseURL": base_url,
                    },
                }
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return environment


def _route_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"managed ACP {field_name} is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(character in normalized for character in ("\r", "\n", "\x00"))
    ):
        raise ValueError(f"managed ACP {field_name} is invalid")
    return normalized


def _gateway_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("managed ACP gateway base URL is invalid")
    return value.rstrip("/")


__all__ = [
    "GatewayRouteSelection",
    "ManagedAcpGatewayCapabilityV1",
    "ManagedAcpGatewayInjection",
    "ManagedAcpGatewayRouteRequired",
    "ManagedAcpGatewaySupport",
    "ManagedAcpGatewayUnsupported",
    "gateway_process_environment",
    "gateway_route_selection",
    "managed_acp_gateway_capability",
    "require_managed_acp_gateway_route",
]
