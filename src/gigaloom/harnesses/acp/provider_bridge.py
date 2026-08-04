"""Capability-first ACP provider bridge resolution and ephemeral adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from packaging.version import InvalidVersion, Version

from gigaloom.harnesses.agent_profiles import VersionPolicy, VersionPolicyKind
from gigaloom.harnesses.acp.process import AcpProcessSpec, pin_acp_process
from gigaloom.harnesses.acp.providers import configure_provider
from gigaloom.native.api import ResolvedGatewayRoute

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient
    from gigaloom.harnesses.acp.contracts import AcpCapabilitySnapshotV1


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
    model_selection="ephemeral_config",
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
            session_model_config_id=None,
        )
    if resolution.strategy is AcpProviderBridgeStrategy.ACP_PROVIDERS:
        return AcpProviderLaunchOverlay((), (), "model")
    raise ValueError("ACP provider bridge adapter is not implemented")


def pin_provider_bridge_process(
    command: tuple[str, ...],
    *,
    cwd: str,
    environment: Mapping[str, str],
    resolution: AcpProviderBridgeResolution | None,
    route: ResolvedGatewayRoute | None,
    api_key: str | None,
    isolated_root: Path,
) -> tuple[AcpProcessSpec, AcpProviderLaunchOverlay | None]:
    """Apply one transient bridge overlay and pin the resulting ACP process."""
    if (resolution is None) != (route is None):
        raise ValueError("ACP provider bridge resolution is incomplete")
    overlay = (
        build_provider_launch_overlay(
            resolution,
            route,
            api_key=api_key,
            isolated_root=isolated_root,
        )
        if resolution is not None and route is not None
        else None
    )
    runtime_environment = dict(environment)
    if overlay is not None:
        runtime_environment.update(overlay.environment)
        codex_home = dict(overlay.environment).get("CODEX_HOME")
        if codex_home is not None:
            Path(codex_home).mkdir(mode=0o700)
    approved_secrets = frozenset(
        name
        for name, _ in (() if overlay is None else overlay.environment)
        if name == "GPT2GIGA_API_KEY"
    )
    process = pin_acp_process(
        (*command, *(() if overlay is None else overlay.arguments)),
        cwd=cwd,
        environment=runtime_environment,
        allowed_environment=frozenset(runtime_environment),
        approved_secret_names=approved_secrets,
    )
    return process, overlay


def apply_provider_bridge_configuration(
    client: AcpClient,
    snapshot: AcpCapabilitySnapshotV1,
    route: ResolvedGatewayRoute,
    resolution: AcpProviderBridgeResolution,
    overlay: AcpProviderLaunchOverlay | None,
    *,
    api_key: str | None,
) -> tuple[str | None, bool]:
    """Configure an advertised provider or return the adapter model selector."""
    if any(
        item.feature == "provider_configuration"
        for item in snapshot.negotiated_features
    ):
        configure_provider(
            client,
            api_type=_provider_api_type(route.provider_protocol),
            base_url=route.credential_free_base_url,
            headers={"Authorization": f"Bearer {api_key or '0'}"},
        )
        return "model", True
    if resolution.strategy is AcpProviderBridgeStrategy.ACP_PROVIDERS:
        return None, False
    return (None if overlay is None else overlay.session_model_config_id), True


def decode_resolved_gateway_route(value: object) -> ResolvedGatewayRoute | None:
    """Decode the strict content-free route binding accepted by managed ACP."""
    if value is None:
        return None
    if isinstance(value, ResolvedGatewayRoute):
        route = value
    elif isinstance(value, Mapping):
        document = dict(value)
        required = {
            "route_id",
            "gateway_id",
            "provider_protocol",
            "credential_free_base_url",
            "public_model_alias",
            "support_status",
            "capability_digest",
            "reason_ids",
        }
        keys = set(document)
        if keys != required and keys != required | {"schema_version"}:
            raise ValueError("managed ACP resolved gateway route is invalid")
        if document.get("schema_version", 1) != 1:
            raise ValueError("managed ACP resolved gateway route is incompatible")
        reasons = document["reason_ids"]
        if not isinstance(reasons, (list, tuple)):
            raise ValueError("managed ACP resolved gateway reasons are invalid")
        route = ResolvedGatewayRoute(
            route_id=_text(document["route_id"]),
            gateway_id=_text(document["gateway_id"]),
            provider_protocol=_text(document["provider_protocol"]),
            credential_free_base_url=_text(document["credential_free_base_url"]),
            public_model_alias=_text(document["public_model_alias"]),
            support_status=_text(document["support_status"]),
            capability_digest=_text(document["capability_digest"]),
            reason_ids=tuple(_text(item) for item in reasons),
        )
    else:
        raise ValueError("managed ACP resolved gateway route is invalid")
    if route.gateway_id != "gpt2giga" or route.support_status == "blocked":
        raise ValueError("managed ACP resolved gateway route is unavailable")
    return route


def require_provider_bridge_route(
    *,
    registry_id: str,
    version: str,
    providers_advertised: bool,
    requested_model: str | None,
    route: ResolvedGatewayRoute | None,
) -> None:
    """Fail closed when an explicit gateway model lacks a verified route."""
    model = (requested_model or "").strip()
    if not model or model == "provider-default" or route is not None:
        return
    bridge = resolve_provider_bridge(
        registry_id=registry_id,
        version=version,
        providers_advertised=providers_advertised,
    )
    if bridge.status is not AcpProviderBridgeStatus.READY:
        raise AcpProviderBridgeUnavailable(bridge.reason_ids[0])
    raise AcpGatewayRouteRequired(
        "managed ACP gateway model requires a resolved route binding"
    )


def resolve_available_provider_bridge(
    *,
    registry_id: str,
    version: str,
    providers_advertised: bool,
    route: ResolvedGatewayRoute | None,
) -> AcpProviderBridgeResolution | None:
    """Resolve an explicit route and reject any native-only bridge."""
    if route is None:
        return None
    resolution = resolve_provider_bridge(
        registry_id=registry_id,
        version=version,
        providers_advertised=providers_advertised,
        advertised_provider_protocols=(route.provider_protocol,),
    )
    if resolution.status is not AcpProviderBridgeStatus.READY:
        raise AcpProviderBridgeUnavailable(resolution.reason_ids[0])
    return resolution


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


def _provider_api_type(provider_protocol: str) -> str:
    if provider_protocol in {"openai_chat_completions", "openai_responses"}:
        return "openai"
    if provider_protocol == "anthropic_messages":
        return "anthropic"
    if provider_protocol == "gemini_generate_content":
        return "gemini"
    raise ValueError("managed ACP provider protocol is unsupported")


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("managed ACP resolved gateway route field is invalid")
    return value
