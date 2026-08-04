"""Capability-first ACP provider bridge resolution and ephemeral adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version

from gigaloom.harnesses.agent_profiles import VersionPolicy, VersionPolicyKind
from gigaloom.native.api import ResolvedGatewayRoute


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
    strategy: AcpProviderBridgeStrategy
    model_selection: str
    adapter_revision: str


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
            "strategy": self.strategy.value if self.strategy is not None else None,
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
    arguments: tuple[str, ...]
    session_model_config_id: str | None

    def projection(self) -> dict[str, object]:
        """Return names and config mechanics without environment values."""
        return {
            "environment_names": [name for name, _ in self.environment],
            "argument_count": len(self.arguments),
            "session_model_config_id": self.session_model_config_id,
        }


_OPENCODE_ADAPTER = AcpProviderAdapterSpec(
    adapter_id="opencode-config-v1",
    registry_id="opencode",
    version_policy=VersionPolicy(
        kind=VersionPolicyKind.REVIEWED_RANGE,
        minimum="1.18.0",
        maximum_exclusive="1.19.0",
    ),
    provider_protocols=("openai_chat_completions",),
    strategy=AcpProviderBridgeStrategy.EPHEMERAL_CONFIG,
    model_selection="ephemeral_config",
    adapter_revision="1",
)
_CODEX_ACP_ADAPTER = AcpProviderAdapterSpec(
    adapter_id="codex-acp-config-v1",
    registry_id="codex-acp",
    version_policy=VersionPolicy(
        kind=VersionPolicyKind.REVIEWED_RANGE,
        minimum="1.1.0",
        maximum_exclusive="1.2.0",
    ),
    provider_protocols=("openai_responses",),
    strategy=AcpProviderBridgeStrategy.EPHEMERAL_CONFIG,
    model_selection="session_config",
    adapter_revision="1",
)
_ADAPTERS = (_CODEX_ACP_ADAPTER, _OPENCODE_ADAPTER)


def resolve_provider_bridge(
    *,
    registry_id: str,
    version: str,
    providers_advertised: bool,
    advertised_provider_protocols: tuple[str, ...] = (),
) -> AcpProviderBridgeResolution:
    """Resolve standard methods first, then one exact reviewed adapter."""
    if providers_advertised:
        protocols = _protocols(advertised_provider_protocols)
        return AcpProviderBridgeResolution(
            status=AcpProviderBridgeStatus.READY,
            strategy=AcpProviderBridgeStrategy.ACP_PROVIDERS,
            provider_protocols=protocols,
            adapter_id=None,
            adapter_revision=None,
            model_selection="acp_model_config",
            reason_ids=(),
        )
    adapter = next(
        (item for item in _ADAPTERS if item.registry_id == registry_id), None
    )
    if adapter is not None and _matches(version, adapter.version_policy):
        return AcpProviderBridgeResolution(
            status=AcpProviderBridgeStatus.READY,
            strategy=adapter.strategy,
            provider_protocols=adapter.provider_protocols,
            adapter_id=adapter.adapter_id,
            adapter_revision=adapter.adapter_revision,
            model_selection=adapter.model_selection,
            reason_ids=(),
        )
    if adapter is not None:
        reason = "agent_version_outside_reviewed_adapter"
    elif registry_id == "amp-acp":
        reason = "agent_has_no_configurable_provider_contract"
    elif registry_id == "qwen-code":
        reason = "qwen_code_adapter_conformance_unverified"
    else:
        reason = "acp_provider_configuration_not_advertised"
    return AcpProviderBridgeResolution(
        status=AcpProviderBridgeStatus.NATIVE_ONLY,
        strategy=None,
        provider_protocols=(),
        adapter_id=None,
        adapter_revision=None,
        model_selection=None,
        reason_ids=(reason,),
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
    base_url = _gateway_url(route.credential_free_base_url)
    secret = api_key or "0"
    if resolution.adapter_id == _OPENCODE_ADAPTER.adapter_id:
        model_id = f"{route.gateway_id}/{route.public_model_alias}"
        config = json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": model_id,
                "provider": {
                    route.gateway_id: {
                        "models": {
                            route.public_model_alias: {
                                "name": route.public_model_alias,
                            }
                        },
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
            environment=(
                ("GPT2GIGA_API_KEY", secret),
                ("OPENCODE_CONFIG_CONTENT", config),
            ),
            arguments=(),
            session_model_config_id=None,
        )
    if resolution.adapter_id == _CODEX_ACP_ADAPTER.adapter_id:
        root = isolated_root.resolve()
        provider = json.dumps(route.gateway_id)
        config = (
            "{ name = "
            f"{provider}, base_url = {json.dumps(base_url)}, "
            'env_key = "GPT2GIGA_API_KEY", wire_api = "responses" }'
        )
        return AcpProviderLaunchOverlay(
            environment=(
                ("CODEX_HOME", (root / "codex-home").as_posix()),
                ("GPT2GIGA_API_KEY", secret),
            ),
            arguments=(
                "-c",
                f"model_providers.{route.gateway_id}={config}",
                "-c",
                f"model_provider={provider}",
                "-c",
                f"model={json.dumps(route.public_model_alias)}",
            ),
            session_model_config_id="model",
        )
    if resolution.strategy is AcpProviderBridgeStrategy.ACP_PROVIDERS:
        return AcpProviderLaunchOverlay((), (), "model")
    raise ValueError("ACP provider bridge adapter is not implemented")


def _matches(version: str, policy: VersionPolicy) -> bool:
    if policy.kind is not VersionPolicyKind.REVIEWED_RANGE:
        return False
    try:
        current = Version(version)
        minimum = Version(policy.minimum or "")
        maximum = Version(policy.maximum_exclusive or "")
    except InvalidVersion:
        return False
    return minimum <= current < maximum


def _protocols(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if len(normalized) > 16 or any(
        not item or len(item) > 128 or "\x00" in item for item in normalized
    ):
        raise ValueError("ACP provider protocols are invalid or unbounded")
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
        raise ValueError("ACP provider bridge base URL is invalid")
    return value.rstrip("/")
