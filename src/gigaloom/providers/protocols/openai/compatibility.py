"""OpenAI-compatible provider templates and hermetic probe contracts."""

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


OPENAI_OFFICIAL_BASE_URL = "https://api.openai.com"
OPENAI_API_KEY_ENVIRONMENT = "OPENAI_API_KEY"
OPENAI_RESPONSES_DIALECT = "openai-responses-v1"
OPENAI_CHAT_COMPLETIONS_DIALECT = "openai-chat-completions-v1"
OPENAI_MODELS_DISCOVERY_STRATEGY = "openai-models-v1"
OPENAI_PROBE_OWNER = "provider-probe:openai-compatible"
OPENAI_DISCOVERY_CACHE_TTL_SECONDS = 300
VLLM_OPENAI_COMPATIBLE_PROFILE_VERSION = "gigaloom.vllm-openai-compatible.v1"
MAX_OPENAI_MODELS = 500
MAX_OPENAI_MODEL_ID_CHARS = 256


from .compatible import (  # noqa: E402
    OpenAIWireAPI,
)


def _build_profile(
    *,
    provider_id: str,
    display_name: str,
    base_url: str,
    route_prefix: str | None,
    wire_api: OpenAIWireAPI,
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
    if authentication.ownership not in {
        AuthenticationOwnership.SECRET_REFERENCE,
        AuthenticationOwnership.NONE,
    }:
        raise ValueError(
            "OpenAI-compatible templates require SecretRef or no authentication"
        )
    evidence = _capability_evidence(wire_api, source=capability_source)
    profile = ProviderProfile(
        id=provider_id,
        revision="pending",
        display_name=display_name,
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        dialect=wire_api.dialect,
        base_url=base_url,
        route_prefix=route_prefix,
        authentication=authentication,
        ownership=ownership,
        capability_evidence=evidence,
        default_models=default_models,
        proxy_policy_ref=proxy_policy_ref,
        tls_policy_ref=tls_policy_ref,
        egress_policy_ref=egress_policy_ref,
        offline=offline,
        discovery_strategy=OPENAI_MODELS_DISCOVERY_STRATEGY,
        discovery_cache_ttl_seconds=discovery_cache_ttl_seconds,
    )
    semantic = provider_profile_to_dict(profile)
    semantic.pop("revision")
    return replace(profile, revision=_semantic_revision(semantic))


def _capability_evidence(
    wire_api: OpenAIWireAPI,
    *,
    source: str,
) -> tuple[SnapshotEvidenceRef, ...]:
    contract = {
        "wire_api": wire_api.value,
        "dialect": wire_api.dialect,
        "models_endpoint": "/v1/models",
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


def _openai_compatibility(
    *,
    harness_id: str,
    dialects: tuple[str, ...],
    transports: tuple[ExecutionTransport, ...],
) -> AdapterProtocolCompatibility:
    adapter_version = _adapter_version()
    semantic = {
        "harness_id": harness_id,
        "adapter_version": adapter_version,
        "protocol": ProviderProtocol.OPENAI_COMPATIBLE.value,
        "dialects": dialects,
        "transports": tuple(item.value for item in transports),
        "capabilities": ("chat", "streaming", "usage"),
        "native_auth": False,
    }
    revision = _semantic_revision(semantic)
    return AdapterProtocolCompatibility(
        id=f"openai-compatible-{harness_id}",
        revision=revision,
        harness_id=harness_id,
        adapter_version=adapter_version,
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        dialects=dialects,
        transports=transports,
        capabilities=("chat", "streaming", "usage"),
        native_auth=False,
        evidence=(
            SnapshotEvidenceRef(
                id=f"{harness_id}-openai-compatible-fixture",
                revision=revision,
                status="supported",
                source="built-in-contract",
            ),
        ),
    )


def _wire_api_for_profile(profile: ProviderProfile) -> OpenAIWireAPI:
    if not isinstance(profile, ProviderProfile):
        raise ProviderCompatibilityFailure("profile_invalid")
    if profile.protocol is not ProviderProtocol.OPENAI_COMPATIBLE:
        raise ProviderCompatibilityFailure("protocol_incompatible")
    by_dialect = {
        OPENAI_RESPONSES_DIALECT: OpenAIWireAPI.RESPONSES,
        OPENAI_CHAT_COMPLETIONS_DIALECT: OpenAIWireAPI.CHAT_COMPLETIONS,
    }
    try:
        return by_dialect[profile.dialect]
    except KeyError:
        raise ProviderCompatibilityFailure("dialect_incompatible") from None


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
        raise ValueError("OpenAI probe reason code is invalid")
