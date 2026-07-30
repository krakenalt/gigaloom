"""Gemini-compatible provider templates and hermetic probe contracts."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from importlib import metadata
import json
from typing import Any, Mapping

from gpt2giga_harness.execution import (
    ExecutionTransport,
    SnapshotEvidenceRef,
)
from gpt2giga_harness.providers.profiles import (
    AdapterProtocolCompatibility,
    AuthenticationOwnership,
    ModelPurposeDefault,
    ProviderAuthentication,
    ProviderOwnership,
    ProviderProfile,
    ProviderProtocol,
    provider_profile_to_dict,
)
from gpt2giga_harness.providers.registry import (
    ProviderCompatibilityFailure,
)


GEMINI_OFFICIAL_BASE_URL = "https://generativelanguage.googleapis.com"
GEMINI_VERTEX_BASE_URL = "https://aiplatform.googleapis.com"
GEMINI_API_KEY_ENVIRONMENT = "GEMINI_API_KEY"
GOOGLE_API_KEY_ENVIRONMENT = "GOOGLE_API_KEY"
GEMINI_GENERATE_CONTENT_DIALECT = "gemini-generate-content-v1beta"
GEMINI_VERTEX_DIALECT = "gemini-vertex-v1"
GEMINI_MODELS_DISCOVERY_STRATEGY = "gemini-models-v1beta"
GEMINI_VERTEX_DISCOVERY_STRATEGY = "gemini-vertex-models-v1"
GEMINI_PROBE_OWNER = "provider-probe:gemini-compatible"
GEMINI_DISCOVERY_CACHE_TTL_SECONDS = 300
MAX_GEMINI_MODELS = 1000
MAX_GEMINI_MODEL_ID_CHARS = 256


from .compatible import (  # noqa: E402
    GeminiCliAuthMode,
    GeminiNativeClientConfiguration,
    GeminiPlatform,
)


def gemini_cli_configuration_evidence(
    auth_mode: GeminiCliAuthMode,
    profile: ProviderProfile | None = None,
    *,
    cli_version: str,
) -> GeminiNativeClientConfiguration:
    """Describe supported Gemini CLI configuration without reading native state."""
    if not isinstance(auth_mode, GeminiCliAuthMode):
        raise ValueError("Gemini CLI auth mode is invalid")
    if (
        not isinstance(cli_version, str)
        or not cli_version
        or len(cli_version) > 128
        or any(ord(character) < 32 for character in cli_version)
    ):
        raise ValueError("Gemini CLI version evidence is invalid")
    platform: GeminiPlatform | None = None
    if profile is not None:
        platform = _platform_for_profile(profile)
    if auth_mode is GeminiCliAuthMode.GOOGLE_LOGIN:
        if profile is not None:
            raise ValueError("Google login cannot be attached to an endpoint profile")
        ownership = AuthenticationOwnership.PROVIDER_NATIVE
        environment_variables = ("GOOGLE_CLOUD_PROJECT",)
        endpoint_override = False
    else:
        if profile is None or platform is None:
            raise ValueError("Gemini CLI endpoint auth requires a provider profile")
        ownership = profile.authentication.ownership
        if auth_mode is GeminiCliAuthMode.GEMINI_API_KEY:
            if platform is not GeminiPlatform.GEMINI_API or (
                ownership is not AuthenticationOwnership.SECRET_REFERENCE
            ):
                raise ValueError("Gemini API key mode requires a SecretRef API profile")
            environment_variables = (GEMINI_API_KEY_ENVIRONMENT,)
            endpoint_override = False
        elif auth_mode is GeminiCliAuthMode.GATEWAY:
            if platform is not GeminiPlatform.GEMINI_API or ownership not in {
                AuthenticationOwnership.SECRET_REFERENCE,
                AuthenticationOwnership.NONE,
            }:
                raise ValueError(
                    "Gemini gateway mode requires a compatible API profile"
                )
            environment_variables = ("GOOGLE_GEMINI_BASE_URL",)
            if ownership is AuthenticationOwnership.SECRET_REFERENCE:
                environment_variables += (GEMINI_API_KEY_ENVIRONMENT,)
            endpoint_override = True
        else:
            if platform is not GeminiPlatform.VERTEX_AI or ownership not in {
                AuthenticationOwnership.PROVIDER_NATIVE,
                AuthenticationOwnership.SECRET_REFERENCE,
            }:
                raise ValueError("Vertex mode requires a Vertex AI profile")
            environment_variables = (
                "GOOGLE_CLOUD_LOCATION",
                "GOOGLE_CLOUD_PROJECT",
                "GOOGLE_GENAI_USE_VERTEXAI",
            )
            if ownership is AuthenticationOwnership.SECRET_REFERENCE:
                environment_variables += (GOOGLE_API_KEY_ENVIRONMENT,)
            endpoint_override = False
    semantic = {
        "auth_mode": auth_mode.value,
        "provider": (
            {"id": profile.id, "revision": profile.revision}
            if profile is not None
            else None
        ),
        "authentication_ownership": ownership.value,
        "environment_variables": tuple(sorted(environment_variables)),
        "endpoint_override": endpoint_override,
        "oauth_token_access": False,
        "gemini_cli_version": cli_version,
    }
    revision = _semantic_revision(semantic)
    return GeminiNativeClientConfiguration(
        auth_mode=auth_mode,
        provider=profile.ref if profile is not None else None,
        authentication_ownership=ownership,
        settings_auth_type=auth_mode.value,
        environment_variables=environment_variables,
        endpoint_override=endpoint_override,
        oauth_token_access=False,
        evidence=(
            SnapshotEvidenceRef(
                id=f"gemini-cli-{auth_mode.value}-configuration",
                revision=revision,
                status="supported",
                source=f"installed-gemini-cli-{cli_version}",
            ),
        ),
    )


def gemini_cli_api_compatibility() -> AdapterProtocolCompatibility:
    """Return Gemini CLI evidence for API and custom gateway routes."""
    return _gemini_compatibility(
        compatibility_id="gemini-compatible-gemini-cli",
        dialects=(GEMINI_GENERATE_CONTENT_DIALECT,),
        native_auth=False,
    )


def gemini_cli_vertex_compatibility() -> AdapterProtocolCompatibility:
    """Return Gemini CLI evidence for provider-native Vertex AI routes."""
    return _gemini_compatibility(
        compatibility_id="gemini-vertex-gemini-cli",
        dialects=(GEMINI_VERTEX_DIALECT,),
        native_auth=True,
    )


def _build_profile(
    *,
    provider_id: str,
    display_name: str,
    base_url: str,
    route_prefix: str | None,
    platform: GeminiPlatform,
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
    if not isinstance(platform, GeminiPlatform):
        raise ValueError("Gemini platform is invalid")
    _validate_authentication(platform, authentication)
    profile = ProviderProfile(
        id=provider_id,
        revision="pending",
        display_name=display_name,
        protocol=ProviderProtocol.GEMINI_COMPATIBLE,
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
    platform: GeminiPlatform,
    authentication: ProviderAuthentication,
) -> None:
    if not isinstance(authentication, ProviderAuthentication):
        raise ValueError("Gemini authentication is invalid")
    allowed = {
        GeminiPlatform.GEMINI_API: {
            AuthenticationOwnership.SECRET_REFERENCE,
            AuthenticationOwnership.NONE,
        },
        GeminiPlatform.VERTEX_AI: {
            AuthenticationOwnership.PROVIDER_NATIVE,
            AuthenticationOwnership.SECRET_REFERENCE,
        },
    }[platform]
    if authentication.ownership not in allowed:
        raise ValueError(f"{platform.value} authentication ownership is incompatible")


def _capability_evidence(
    platform: GeminiPlatform,
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


def _gemini_compatibility(
    *,
    compatibility_id: str,
    dialects: tuple[str, ...],
    native_auth: bool,
) -> AdapterProtocolCompatibility:
    adapter_version = _adapter_version()
    transports = (
        ExecutionTransport.NATIVE_STRUCTURED,
        ExecutionTransport.NATIVE_TERMINAL,
        ExecutionTransport.ONE_SHOT,
    )
    semantic = {
        "harness_id": "gemini-cli",
        "adapter_version": adapter_version,
        "protocol": ProviderProtocol.GEMINI_COMPATIBLE.value,
        "dialects": dialects,
        "transports": tuple(item.value for item in transports),
        "capabilities": ("chat", "streaming", "usage"),
        "native_auth": native_auth,
        "reviewed_cli_window": "0.46.x",
        "structured_surface": "acp",
    }
    revision = _semantic_revision(semantic)
    return AdapterProtocolCompatibility(
        id=compatibility_id,
        revision=revision,
        harness_id="gemini-cli",
        adapter_version=adapter_version,
        protocol=ProviderProtocol.GEMINI_COMPATIBLE,
        dialects=dialects,
        transports=transports,
        capabilities=("chat", "streaming", "usage"),
        native_auth=native_auth,
        evidence=(
            SnapshotEvidenceRef(
                id=f"{compatibility_id}-fixture",
                revision=revision,
                status="supported",
                source="built-in-gemini-cli-0.46-contract",
            ),
        ),
    )


def _platform_for_profile(profile: ProviderProfile) -> GeminiPlatform:
    if not isinstance(profile, ProviderProfile):
        raise ProviderCompatibilityFailure("profile_invalid")
    if profile.protocol is not ProviderProtocol.GEMINI_COMPATIBLE:
        raise ProviderCompatibilityFailure("protocol_incompatible")
    by_dialect = {item.dialect: item for item in GeminiPlatform}
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
    if not isinstance(values, tuple) or len(values) > MAX_GEMINI_MODELS:
        raise ValueError("Gemini normalized models are invalid")
    return tuple(sorted({_normalize_model(value) for value in values}))


def _normalize_model(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Gemini model id is invalid")
    normalized = value.strip()
    if len(normalized) > MAX_GEMINI_MODEL_ID_CHARS or any(
        ord(character) < 32 for character in normalized
    ):
        raise ValueError("Gemini model id is invalid")
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
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout
        return "0.0.0+source"


def _validate_reason_code(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_-"
            for character in value
        )
    ):
        raise ValueError("Gemini reason code is invalid")
