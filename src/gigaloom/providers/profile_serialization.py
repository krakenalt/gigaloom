"""Provider profile serialization and legacy route migration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from gigaloom.execution import ProviderRef, SnapshotEvidenceRef
from gigaloom.secrets import (
    SecretReference,
    SecretReferenceKind,
    secret_reference_to_dict,
)

from .profile_builtins import (
    _LEGACY_CAPABILITIES,
    _canonical_base_url,
    _authentication_from_dict,
    _authentication_to_dict,
    _enum_value,
    _evidence_from_list,
    _evidence_to_dict,
    _legacy_protocol,
    _model_defaults_from_list,
    _optional_text,
    _required_bool,
    _required_int,
    _required_text,
    _strict_mapping,
    _validate_text,
)
from .profiles import (
    PROVIDER_PROFILE_SCHEMA_VERSION,
    ROUTE_PROFILE_SCHEMA_VERSION,
    AuthenticationOwnership,
    ModelPurpose,
    ModelPurposeDefault,
    ProviderAuthentication,
    ProviderOwnership,
    ProviderProfile,
    ProviderProtocol,
    RouteProfile,
)


def migrate_legacy_provider_route(
    *,
    proxy_url: str,
    api_mode: str,
    harness_id: str,
    model: str,
    purpose: ModelPurpose = ModelPurpose.CODING,
    secret_reference: SecretReference | None = None,
) -> tuple[ProviderProfile, RouteProfile]:
    """Map legacy proxy/api-mode values without changing their effective URL."""
    protocol = _legacy_protocol(harness_id)
    normalized_mode = str(api_mode).strip().lower()
    if normalized_mode not in {"v1", "v2"}:
        raise ValueError("legacy api_mode must be v1 or v2")
    if not isinstance(purpose, ModelPurpose):
        raise ValueError("legacy model purpose is invalid")
    _validate_text(model, field_name="legacy model")
    base_url = _canonical_base_url(proxy_url)
    dialect = f"gpt2giga-{normalized_mode}"
    reference = secret_reference or SecretReference(
        kind=SecretReferenceKind.ENVIRONMENT,
        name="GPT2GIGA_HARNESS_API_KEY",
    )
    semantic = {
        "proxy_url": base_url,
        "api_mode": normalized_mode,
        "protocol": protocol.value,
        "harness_id": harness_id,
        "model": model,
        "purpose": purpose.value,
        "secret_reference": secret_reference_to_dict(reference),
    }
    fingerprint = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    evidence = (
        SnapshotEvidenceRef(
            id=f"legacy-{harness_id}-{normalized_mode}",
            revision=fingerprint,
            status="supported",
            source="legacy-migration",
        ),
    )
    capabilities = tuple(
        SnapshotEvidenceRef(
            id=capability,
            revision=fingerprint,
            status="supported",
            source="legacy-migration",
        )
        for capability in _LEGACY_CAPABILITIES[harness_id]
    )
    provider = ProviderProfile(
        id=f"legacy-gpt2giga-{protocol.value.replace('_', '-')}",
        revision=fingerprint,
        display_name="Migrated gpt2giga proxy",
        protocol=protocol,
        dialect=dialect,
        base_url=base_url,
        route_prefix=f"/{normalized_mode}",
        authentication=ProviderAuthentication(
            ownership=AuthenticationOwnership.SECRET_REFERENCE,
            secret_reference=reference,
        ),
        ownership=ProviderOwnership.MIGRATED_LEGACY,
        capability_evidence=capabilities,
        default_models=(ModelPurposeDefault(purpose, model),),
        discovery_strategy="legacy-models-route",
    )
    route = RouteProfile(
        id=f"legacy-{harness_id}-{normalized_mode}-{purpose.value}",
        revision=fingerprint,
        provider=provider.ref,
        protocol=protocol,
        dialect=dialect,
        effective_base_url=provider.effective_base_url,
        purpose=purpose,
        model=model,
        authentication_ownership=AuthenticationOwnership.SECRET_REFERENCE,
        capability_evidence=(*capabilities, *evidence),
    )
    return provider, route


def provider_profile_to_dict(profile: ProviderProfile) -> dict[str, Any]:
    """Serialize a strict, reference-only provider profile."""
    return {
        "schema_version": profile.schema_version,
        "id": profile.id,
        "revision": profile.revision,
        "display_name": profile.display_name,
        "protocol": profile.protocol.value,
        "dialect": profile.dialect,
        "base_url": profile.base_url,
        "route_prefix": profile.route_prefix,
        "authentication": _authentication_to_dict(profile.authentication),
        "ownership": profile.ownership.value,
        "capability_evidence": [
            _evidence_to_dict(item) for item in profile.capability_evidence
        ],
        "default_models": [
            {"purpose": item.purpose.value, "model": item.model}
            for item in profile.default_models
        ],
        "tls_policy_ref": profile.tls_policy_ref,
        "proxy_policy_ref": profile.proxy_policy_ref,
        "egress_policy_ref": profile.egress_policy_ref,
        "offline": profile.offline,
        "discovery_strategy": profile.discovery_strategy,
        "discovery_cache_ttl_seconds": profile.discovery_cache_ttl_seconds,
    }


def provider_profile_from_dict(data: Mapping[str, Any]) -> ProviderProfile:
    """Parse a forward-only provider profile and reject value-bearing extras."""
    mapping = _strict_mapping(
        data,
        allowed={
            "schema_version",
            "id",
            "revision",
            "display_name",
            "protocol",
            "dialect",
            "base_url",
            "route_prefix",
            "authentication",
            "ownership",
            "capability_evidence",
            "default_models",
            "tls_policy_ref",
            "proxy_policy_ref",
            "egress_policy_ref",
            "offline",
            "discovery_strategy",
            "discovery_cache_ttl_seconds",
        },
        field_name="provider profile",
    )
    if mapping.get("schema_version") != PROVIDER_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported provider profile schema_version")
    return ProviderProfile(
        id=_required_text(mapping.get("id"), field_name="provider id"),
        revision=_required_text(
            mapping.get("revision"), field_name="provider revision"
        ),
        display_name=_required_text(
            mapping.get("display_name"), field_name="provider display_name"
        ),
        protocol=_enum_value(
            ProviderProtocol,
            mapping.get("protocol"),
            field_name="provider protocol",
        ),
        dialect=_required_text(mapping.get("dialect"), field_name="provider dialect"),
        base_url=_required_text(
            mapping.get("base_url"), field_name="provider base_url"
        ),
        route_prefix=_optional_text(mapping.get("route_prefix")),
        authentication=_authentication_from_dict(mapping.get("authentication")),
        ownership=_enum_value(
            ProviderOwnership,
            mapping.get("ownership"),
            field_name="provider ownership",
        ),
        capability_evidence=_evidence_from_list(
            mapping.get("capability_evidence"),
            field_name="provider capability_evidence",
        ),
        default_models=_model_defaults_from_list(mapping.get("default_models")),
        tls_policy_ref=_optional_text(mapping.get("tls_policy_ref")),
        proxy_policy_ref=_optional_text(mapping.get("proxy_policy_ref")),
        egress_policy_ref=_optional_text(mapping.get("egress_policy_ref")),
        offline=_required_bool(mapping.get("offline"), field_name="provider offline"),
        discovery_strategy=_required_text(
            mapping.get("discovery_strategy"),
            field_name="provider discovery_strategy",
        ),
        discovery_cache_ttl_seconds=_required_int(
            mapping.get("discovery_cache_ttl_seconds"),
            field_name="provider discovery_cache_ttl_seconds",
        ),
        schema_version=PROVIDER_PROFILE_SCHEMA_VERSION,
    )


def route_profile_to_dict(profile: RouteProfile) -> dict[str, Any]:
    """Serialize a strict provider-bound route profile."""
    return {
        "schema_version": profile.schema_version,
        "id": profile.id,
        "revision": profile.revision,
        "provider": {"id": profile.provider.id, "revision": profile.provider.revision},
        "protocol": profile.protocol.value,
        "dialect": profile.dialect,
        "effective_base_url": profile.effective_base_url,
        "purpose": profile.purpose.value,
        "model": profile.model,
        "authentication_ownership": profile.authentication_ownership.value,
        "capability_evidence": [
            _evidence_to_dict(item) for item in profile.capability_evidence
        ],
    }


def route_profile_from_dict(data: Mapping[str, Any]) -> RouteProfile:
    """Parse a forward-only route profile."""
    mapping = _strict_mapping(
        data,
        allowed={
            "schema_version",
            "id",
            "revision",
            "provider",
            "protocol",
            "dialect",
            "effective_base_url",
            "purpose",
            "model",
            "authentication_ownership",
            "capability_evidence",
        },
        field_name="route profile",
    )
    if mapping.get("schema_version") != ROUTE_PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported route profile schema_version")
    raw_provider = _strict_mapping(
        mapping.get("provider"),
        allowed={"id", "revision"},
        field_name="route provider",
    )
    return RouteProfile(
        id=_required_text(mapping.get("id"), field_name="route id"),
        revision=_required_text(mapping.get("revision"), field_name="route revision"),
        provider=ProviderRef(
            _required_text(raw_provider.get("id"), field_name="provider id"),
            _required_text(
                raw_provider.get("revision"), field_name="provider revision"
            ),
        ),
        protocol=_enum_value(
            ProviderProtocol,
            mapping.get("protocol"),
            field_name="route protocol",
        ),
        dialect=_required_text(mapping.get("dialect"), field_name="route dialect"),
        effective_base_url=_required_text(
            mapping.get("effective_base_url"),
            field_name="route effective_base_url",
        ),
        purpose=_enum_value(
            ModelPurpose,
            mapping.get("purpose"),
            field_name="route purpose",
        ),
        model=_required_text(mapping.get("model"), field_name="route model"),
        authentication_ownership=_enum_value(
            AuthenticationOwnership,
            mapping.get("authentication_ownership"),
            field_name="route authentication ownership",
        ),
        capability_evidence=_evidence_from_list(
            mapping.get("capability_evidence"),
            field_name="route capability_evidence",
        ),
        schema_version=ROUTE_PROFILE_SCHEMA_VERSION,
    )
