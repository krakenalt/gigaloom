"""Anthropic-compatible provider templates and hermetic probe contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib import metadata
import json
from typing import Any, Mapping

from gigaloom.execution import (
    ExecutionTransport,
    SnapshotEvidenceRef,
)
from gigaloom.providers.profiles import (
    AdapterProtocolCompatibility,
    AuthenticationOwnership,
    ModelPurposeDefault,
    ProviderAuthentication,
    ProviderOwnership,
    ProviderProfile,
    ProviderProtocol,
    provider_profile_to_dict,
)
from gigaloom.providers.registry import (
    ProviderCompatibilityFailure,
)


ANTHROPIC_OFFICIAL_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_API_KEY_ENVIRONMENT = "ANTHROPIC_API_KEY"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_MESSAGES_DIALECT = "anthropic-messages-v1"
ANTHROPIC_BEDROCK_DIALECT = "anthropic-bedrock-v1"
ANTHROPIC_VERTEX_DIALECT = "anthropic-vertex-v1"
ANTHROPIC_FOUNDRY_DIALECT = "anthropic-foundry-v1"
ANTHROPIC_MODELS_DISCOVERY_STRATEGY = "anthropic-models-v1"
ANTHROPIC_PLATFORM_DISCOVERY_STRATEGY = "anthropic-platform-models-v1"
ANTHROPIC_PROBE_OWNER = "provider-probe:anthropic-compatible"
ANTHROPIC_DISCOVERY_CACHE_TTL_SECONDS = 300
MAX_ANTHROPIC_MODELS = 500
MAX_ANTHROPIC_MODEL_ID_CHARS = 256


from .compatible import (  # noqa: E402
    AnthropicPlatform,
)


def _build_profile(
    *,
    provider_id: str,
    display_name: str,
    base_url: str,
    route_prefix: str | None,
    platform: AnthropicPlatform,
    authentication: ProviderAuthentication,
    ownership: ProviderOwnership,
    default_models: tuple[ModelPurposeDefault, ...],
    discovery_cache_ttl_seconds: int,
    capability_source: str,
    proxy_policy_ref: str | None = None,
    tls_policy_ref: str | None = None,
    egress_policy_ref: str | None = None,
    offline: bool = False,
) -> ProviderProfile:
    if not isinstance(platform, AnthropicPlatform):
        raise ValueError("Anthropic platform is invalid")
    _validate_authentication(platform, authentication)
    profile = ProviderProfile(
        id=provider_id,
        revision="pending",
        display_name=display_name,
        protocol=ProviderProtocol.ANTHROPIC_COMPATIBLE,
        dialect=platform.dialect,
        base_url=base_url,
        route_prefix=route_prefix,
        authentication=authentication,
        ownership=ownership,
        capability_evidence=_capability_evidence(platform, source=capability_source),
        default_models=default_models,
        proxy_policy_ref=proxy_policy_ref,
        tls_policy_ref=tls_policy_ref,
        egress_policy_ref=egress_policy_ref,
        offline=offline,
        discovery_strategy=platform.discovery_strategy,
        discovery_cache_ttl_seconds=discovery_cache_ttl_seconds,
    )
    semantic = provider_profile_to_dict(profile)
    semantic.pop("revision")
    return replace(profile, revision=_semantic_revision(semantic))


def _validate_authentication(
    platform: AnthropicPlatform,
    authentication: ProviderAuthentication,
) -> None:
    if not isinstance(authentication, ProviderAuthentication):
        raise ValueError("Anthropic authentication is invalid")
    ownership = authentication.ownership
    allowed = {
        AnthropicPlatform.ANTHROPIC_API: {
            AuthenticationOwnership.SECRET_REFERENCE,
            AuthenticationOwnership.NONE,
        },
        AnthropicPlatform.AMAZON_BEDROCK: {
            AuthenticationOwnership.PROVIDER_NATIVE,
        },
        AnthropicPlatform.GOOGLE_VERTEX: {
            AuthenticationOwnership.PROVIDER_NATIVE,
        },
        AnthropicPlatform.MICROSOFT_FOUNDRY: {
            AuthenticationOwnership.PROVIDER_NATIVE,
            AuthenticationOwnership.SECRET_REFERENCE,
        },
    }[platform]
    if ownership not in allowed:
        raise ValueError(f"{platform.value} authentication ownership is incompatible")


def _capability_evidence(
    platform: AnthropicPlatform,
    *,
    source: str,
) -> tuple[SnapshotEvidenceRef, ...]:
    contract = {
        "platform": platform.value,
        "dialect": platform.dialect,
        "models_discovery": platform.discovery_strategy,
        "capabilities": {
            "chat": "supported",
            "images": "model-dependent",
            "reasoning": "model-dependent",
            "streaming": "supported",
            "structured-output": "model-dependent",
            "tools": "model-dependent",
            "usage": "supported",
        },
    }
    revision = _semantic_revision(contract)
    return tuple(
        SnapshotEvidenceRef(
            id=capability,
            revision=revision,
            status=status,
            source=source,
        )
        for capability, status in contract["capabilities"].items()
    )


def _claude_compatibility(
    *,
    compatibility_id: str,
    dialects: tuple[str, ...],
    native_auth: bool,
) -> AdapterProtocolCompatibility:
    adapter_version = _adapter_version()
    transports = (
        ExecutionTransport.NATIVE_TERMINAL,
        ExecutionTransport.ONE_SHOT,
    )
    semantic = {
        "harness_id": "claude-code",
        "adapter_version": adapter_version,
        "protocol": ProviderProtocol.ANTHROPIC_COMPATIBLE.value,
        "dialects": dialects,
        "transports": tuple(item.value for item in transports),
        "capabilities": ("chat", "streaming", "usage"),
        "native_auth": native_auth,
        "structured_transport": "blocked-by-n2-04",
    }
    revision = _semantic_revision(semantic)
    return AdapterProtocolCompatibility(
        id=compatibility_id,
        revision=revision,
        harness_id="claude-code",
        adapter_version=adapter_version,
        protocol=ProviderProtocol.ANTHROPIC_COMPATIBLE,
        dialects=dialects,
        transports=transports,
        capabilities=("chat", "streaming", "usage"),
        native_auth=native_auth,
        evidence=(
            SnapshotEvidenceRef(
                id=f"{compatibility_id}-fixture",
                revision=revision,
                status="supported",
                source="built-in-contract",
            ),
        ),
    )


def _platform_for_profile(profile: ProviderProfile) -> AnthropicPlatform:
    if not isinstance(profile, ProviderProfile):
        raise ProviderCompatibilityFailure("profile_invalid")
    if profile.protocol is not ProviderProtocol.ANTHROPIC_COMPATIBLE:
        raise ProviderCompatibilityFailure("protocol_incompatible")
    by_dialect = {item.dialect: item for item in AnthropicPlatform}
    try:
        platform = by_dialect[profile.dialect]
    except KeyError:
        raise ProviderCompatibilityFailure("dialect_incompatible") from None
    try:
        _validate_authentication(platform, profile.authentication)
    except ValueError:
        raise ProviderCompatibilityFailure(
            "authentication_ownership_incompatible"
        ) from None
    return platform


def _normalize_models(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > MAX_ANTHROPIC_MODELS:
        raise ValueError("Anthropic normalized models are invalid")
    return tuple(sorted({_normalize_model(value) for value in values}))


def _normalize_model(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Anthropic model id is invalid")
    normalized = value.strip()
    if len(normalized) > MAX_ANTHROPIC_MODEL_ID_CHARS or any(
        ord(character) < 32 for character in normalized
    ):
        raise ValueError("Anthropic model id is invalid")
    return normalized


def _semantic_revision(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _adapter_version() -> str:
    try:
        return metadata.version("gigaloom")
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout only
        return "0"


def _validate_reason_code(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not all(character.isalnum() or character in "._-" for character in value)
    ):
        raise ValueError("Anthropic probe reason code is invalid")
