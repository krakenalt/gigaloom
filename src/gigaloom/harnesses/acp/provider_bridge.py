"""Capability-first ACP provider bridge resolution and ephemeral adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
import re

from gigaloom.harnesses.agent_profiles import VersionPolicy, VersionPolicyKind
from gigaloom.native.api import ResolvedGatewayRoute


class AcpGatewayRouteRequired(ValueError):
    """Raised when gateway selection lacks one resolved route binding."""


class AcpProviderBridgeUnavailable(ValueError):
    """Raised when an explicit gateway route has no verified ACP bridge."""

    def __init__(self, reason_id: str) -> None:
        super().__init__(reason_id)
        self.reason_id = reason_id


class AcpProviderBridgeStrategy(str, Enum):
    """Supported ways to configure an ACP agent's downstream provider."""

    ACP_PROVIDERS = "acp_providers"
    OPENAI_ENV = "openai_env"
    EPHEMERAL_CONFIG = "ephemeral_config"


class AcpProviderBridgeStatus(str, Enum):
    """Content-free provider bridge readiness."""

    READY = "ready"
    NATIVE_ONLY = "native_only"


@dataclass(frozen=True, slots=True)
class AcpProviderAdapterSpec:
    """One exact built-in adapter guarded by a reviewed version window."""

    adapter_id: str
    registry_id: str
    version_policy: VersionPolicy
    provider_protocols: tuple[str, ...]
    strategy: AcpProviderBridgeStrategy = AcpProviderBridgeStrategy.EPHEMERAL_CONFIG
    model_selection: str = "ephemeral_config"
    adapter_revision: str = "1"


@dataclass(frozen=True, slots=True)
class AcpProviderBridgeResolution:
    """Content-free resolution of one installed agent's provider bridge."""

    status: AcpProviderBridgeStatus
    strategy: AcpProviderBridgeStrategy | None
    provider_protocols: tuple[str, ...]
    adapter_id: str | None
    adapter_revision: str | None
    model_selection: str | None
    reason_ids: tuple[str, ...]

    def projection(self) -> dict[str, object]:
        """Return the persisted diagnostic shape without runtime secrets."""
        return {
            "status": self.status.value,
            "strategy": self.strategy.value if self.strategy else None,
            "protocols": list(self.provider_protocols),
            "adapter_id": self.adapter_id,
            "adapter_revision": self.adapter_revision,
            "model_selection": self.model_selection,
            "reason_ids": list(self.reason_ids),
        }


@dataclass(frozen=True, slots=True)
class AcpProviderLaunchOverlay:
    """Transient process inputs; secret values never enter its representation."""

    environment: tuple[tuple[str, str], ...] = field(repr=False)
    arguments: tuple[str, ...] = ()
    session_model_config_id: str | None = None

    def projection(self) -> dict[str, object]:
        """Return names and config mechanics without environment values."""
        return {
            "environment_names": [name for name, _ in self.environment],
            "argument_count": len(self.arguments),
            "session_model_config_id": self.session_model_config_id,
        }


def _policy(minimum: str, maximum: str) -> VersionPolicy:
    return VersionPolicy(
        VersionPolicyKind.REVIEWED_RANGE,
        minimum=minimum,
        maximum_exclusive=maximum,
    )


_ADAPTERS = {
    "codex-acp": AcpProviderAdapterSpec(
        "codex-acp-config-v1",
        "codex-acp",
        _policy("1.1.0", "1.2.0"),
        ("openai_responses",),
    ),
    "opencode": AcpProviderAdapterSpec(
        "opencode-config-v1",
        "opencode",
        _policy("1.18.0", "1.19.0"),
        ("openai_chat_completions",),
    ),
}
_NATIVE_ONLY_REASONS = {
    "amp-acp": "agent_has_no_configurable_provider_contract",
    "qwen-code": "qwen_code_adapter_conformance_unverified",
}


def resolve_provider_bridge(
    *,
    registry_id: str,
    version: str,
    providers_advertised: bool,
    advertised_provider_protocols: tuple[str, ...] = (),
) -> AcpProviderBridgeResolution:
    """Resolve standard methods first, then one exact reviewed adapter."""
    if providers_advertised:
        return AcpProviderBridgeResolution(
            AcpProviderBridgeStatus.READY,
            AcpProviderBridgeStrategy.ACP_PROVIDERS,
            _protocols(advertised_provider_protocols),
            None,
            None,
            "acp_model_config",
            (),
        )
    adapter = _ADAPTERS.get(registry_id)
    if adapter and _matches(version, adapter.version_policy):
        return AcpProviderBridgeResolution(
            AcpProviderBridgeStatus.READY,
            adapter.strategy,
            adapter.provider_protocols,
            adapter.adapter_id,
            adapter.adapter_revision,
            adapter.model_selection,
            (),
        )
    reason = (
        "agent_version_outside_reviewed_adapter"
        if adapter
        else _NATIVE_ONLY_REASONS.get(
            registry_id, "acp_provider_configuration_not_advertised"
        )
    )
    return AcpProviderBridgeResolution(
        AcpProviderBridgeStatus.NATIVE_ONLY,
        None,
        (),
        None,
        None,
        None,
        (reason,),
    )


def build_provider_launch_overlay(
    resolution: AcpProviderBridgeResolution,
    route: ResolvedGatewayRoute,
    *,
    api_key: str | None,
    isolated_root: Path,
) -> AcpProviderLaunchOverlay:
    """Build deterministic adapter inputs under an already isolated root."""
    if resolution.status is not AcpProviderBridgeStatus.READY:
        raise ValueError("ACP provider bridge is native-only")
    if route.provider_protocol not in resolution.provider_protocols:
        raise ValueError("ACP provider bridge does not support the resolved protocol")
    base_url = route.credential_free_base_url.rstrip("/")
    secret = api_key or "0"
    if resolution.adapter_id == "opencode-config-v1":
        alias = route.public_model_alias
        config = json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": f"{route.gateway_id}/{alias}",
                "provider": {
                    route.gateway_id: {
                        "models": {alias: {"name": alias}},
                        "name": route.gateway_id,
                        "npm": "@ai-sdk/openai-compatible",
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
        return AcpProviderLaunchOverlay(
            (("GPT2GIGA_API_KEY", secret), ("OPENCODE_CONFIG_CONTENT", config))
        )
    if resolution.adapter_id == "codex-acp-config-v1":
        provider = json.dumps(route.gateway_id)
        config = (
            f"{{ name = {provider}, base_url = {json.dumps(base_url)}, "
            'env_key = "GPT2GIGA_API_KEY", wire_api = "responses" }'
        )
        return AcpProviderLaunchOverlay(
            (
                ("CODEX_HOME", (isolated_root.resolve() / "codex-home").as_posix()),
                ("GPT2GIGA_API_KEY", secret),
            ),
            (
                "-c",
                f"model_providers.{route.gateway_id}={config}",
                "-c",
                f"model_provider={provider}",
                "-c",
                f"model={json.dumps(route.public_model_alias)}",
            ),
        )
    if resolution.strategy is AcpProviderBridgeStrategy.ACP_PROVIDERS:
        return AcpProviderLaunchOverlay((), session_model_config_id="model")
    raise ValueError("ACP provider bridge adapter is not implemented")


def decode_resolved_gateway_route(value: object) -> ResolvedGatewayRoute | None:
    """Decode the strict content-free route binding accepted by managed ACP."""
    if value is None or isinstance(value, ResolvedGatewayRoute):
        route = value
    elif isinstance(value, Mapping):
        document = dict(value)
        if document.pop("schema_version", 1) != 1:
            raise ValueError("managed ACP resolved gateway route is incompatible")
        reasons = document.get("reason_ids")
        if not isinstance(reasons, (list, tuple)):
            raise ValueError("managed ACP resolved gateway reasons are invalid")
        document["reason_ids"] = tuple(reasons)
        try:
            route = ResolvedGatewayRoute(**document)
        except (TypeError, ValueError) as exc:
            raise ValueError("managed ACP resolved gateway route is invalid") from exc
    else:
        raise ValueError("managed ACP resolved gateway route is invalid")
    if route and (route.gateway_id != "gpt2giga" or route.support_status == "blocked"):
        raise ValueError("managed ACP resolved gateway route is unavailable")
    return route


def _matches(value: str, policy: VersionPolicy) -> bool:
    current = _version(value)
    minimum = _version(policy.minimum or "")
    maximum = _version(policy.maximum_exclusive or "")
    return (
        policy.kind is VersionPolicyKind.REVIEWED_RANGE
        and current is not None
        and minimum is not None
        and maximum is not None
        and minimum <= current < maximum
    )


def _version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else None  # type: ignore[return-value]


def _protocols(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if len(normalized) > 16 or any(
        not item or len(item) > 128 or "\x00" in item for item in normalized
    ):
        raise ValueError("ACP provider protocols are invalid or unbounded")
    return normalized


_PROVIDER_API_TYPES = {
    "anthropic_messages": "anthropic",
    "gemini_generate_content": "gemini",
    "openai_chat_completions": "openai",
    "openai_responses": "openai",
}


def provider_api_type(provider_protocol: str) -> str:
    """Map one reviewed route protocol to the ACP provider vocabulary."""
    try:
        return _PROVIDER_API_TYPES[provider_protocol]
    except KeyError as exc:
        raise ValueError("managed ACP provider protocol is unsupported") from exc
