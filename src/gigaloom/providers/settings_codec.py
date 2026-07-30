"""Backend-authoritative provider Settings application service."""

from __future__ import annotations

from typing import Any, Mapping

from gigaloom.providers.protocols.anthropic.compatible import (
    ANTHROPIC_BEDROCK_DIALECT,
    ANTHROPIC_FOUNDRY_DIALECT,
    ANTHROPIC_MESSAGES_DIALECT,
    ANTHROPIC_VERTEX_DIALECT,
    AnthropicPlatform,
    anthropic_cloud_profile,
    anthropic_compatible_route,
    custom_anthropic_compatible_profile,
)
from gigaloom.providers.protocols.gemini.compatible import (
    GEMINI_GENERATE_CONTENT_DIALECT,
    GEMINI_VERTEX_DIALECT,
    custom_gemini_compatible_profile,
    gemini_compatible_route,
    vertex_ai_gemini_profile,
)
from gigaloom.providers.protocols.openai.compatible import (
    OPENAI_CHAT_COMPLETIONS_DIALECT,
    OPENAI_RESPONSES_DIALECT,
    OpenAIWireAPI,
    custom_openai_compatible_profile,
    openai_compatible_route,
)
from gigaloom.providers.profiles import (
    AuthenticationOwnership,
    ModelPurpose,
    ModelPurposeDefault,
    ProviderAuthentication,
    ProviderOwnership,
    ProviderProfile,
    ProviderProtocol,
    RouteProfile,
)
from gigaloom.providers.registry import (
    ProviderHealthSnapshot,
    ProviderRegistryEntry,
)
from gigaloom.secrets import SecretReference, SecretReferenceKind

from .settings import ProviderSettingsValidationError


PROVIDER_SETTINGS_FIELDS = frozenset(
    {
        "display_name",
        "protocol",
        "dialect",
        "base_url",
        "route_prefix",
        "authentication",
        "default_models",
        "enabled",
        "offline",
    }
)

PROVIDER_EFFECTS = {
    "registry": "authoritative_read_back",
    "new_runs": "new_session_required",
    "structured_sessions": "fork_or_new_session_required",
    "managed_homes": "restart_required",
}

PROVIDER_TEMPLATES = (
    {
        "id": "openai-responses",
        "title": "OpenAI-compatible Responses",
        "protocol": ProviderProtocol.OPENAI_COMPATIBLE.value,
        "dialect": OPENAI_RESPONSES_DIALECT,
        "base_url": "https://api.openai.com",
        "route_prefix": "/v1",
        "authentication": "secret_reference",
        "secret_reference_name": "OPENAI_API_KEY",
    },
    {
        "id": "openai-chat-completions",
        "title": "OpenAI-compatible Chat Completions",
        "protocol": ProviderProtocol.OPENAI_COMPATIBLE.value,
        "dialect": OPENAI_CHAT_COMPLETIONS_DIALECT,
        "base_url": "https://api.openai.com",
        "route_prefix": "/v1",
        "authentication": "secret_reference",
        "secret_reference_name": "OPENAI_API_KEY",
    },
    {
        "id": "anthropic-messages",
        "title": "Anthropic-compatible Messages",
        "protocol": ProviderProtocol.ANTHROPIC_COMPATIBLE.value,
        "dialect": ANTHROPIC_MESSAGES_DIALECT,
        "base_url": "https://api.anthropic.com",
        "route_prefix": "/v1",
        "authentication": "secret_reference",
        "secret_reference_name": "ANTHROPIC_API_KEY",
    },
    {
        "id": "gemini-generate-content",
        "title": "Gemini-compatible GenerateContent",
        "protocol": ProviderProtocol.GEMINI_COMPATIBLE.value,
        "dialect": GEMINI_GENERATE_CONTENT_DIALECT,
        "base_url": "https://generativelanguage.googleapis.com",
        "route_prefix": "/v1beta",
        "authentication": "secret_reference",
        "secret_reference_name": "GEMINI_API_KEY",
    },
    {
        "id": "gemini-vertex",
        "title": "Gemini on Vertex AI",
        "protocol": ProviderProtocol.GEMINI_COMPATIBLE.value,
        "dialect": GEMINI_VERTEX_DIALECT,
        "base_url": "https://aiplatform.googleapis.com",
        "route_prefix": None,
        "authentication": "provider_native",
        "secret_reference_name": None,
    },
)


def _normalize_spec(
    provider_id: str,
    payload: Mapping[str, Any],
    *,
    current: ProviderRegistryEntry | None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ProviderSettingsValidationError({"provider": "expected an object"})
    unknown = sorted(set(payload) - PROVIDER_SETTINGS_FIELDS)
    if unknown:
        raise ProviderSettingsValidationError(
            {field: "unknown provider setting" for field in unknown}
        )
    seed = _entry_spec(current) if current is not None else {}
    spec = {**seed, **dict(payload)}
    if current is not None and isinstance(payload.get("authentication"), Mapping):
        spec["authentication"] = {
            **seed["authentication"],
            **dict(payload["authentication"]),
        }
    if current is not None and isinstance(payload.get("default_models"), Mapping):
        spec["default_models"] = {
            **seed["default_models"],
            **dict(payload["default_models"]),
        }
    errors: dict[str, str] = {}
    if not isinstance(provider_id, str) or not provider_id.strip():
        errors["provider_id"] = "provider id is required"
    for field in ("display_name", "protocol", "base_url"):
        value = spec.get(field)
        if not isinstance(value, str) or not value.strip():
            errors[field] = f"{field.replace('_', ' ')} is required"
    try:
        protocol = ProviderProtocol(spec.get("protocol"))
    except (TypeError, ValueError):
        errors["protocol"] = (
            "expected openai_compatible, anthropic_compatible, or gemini_compatible"
        )
        protocol = None
    dialect = spec.get("dialect") or _default_dialect(protocol)
    if not isinstance(dialect, str) or not dialect:
        errors["dialect"] = "select a reviewed protocol dialect"
    elif protocol is not None and dialect not in _dialects(protocol):
        errors["dialect"] = "dialect is not reviewed for the selected protocol"
    route_prefix = spec.get("route_prefix")
    if route_prefix is not None and not isinstance(route_prefix, str):
        errors["route_prefix"] = "route prefix must be text or null"
    for field in ("enabled", "offline"):
        if not isinstance(spec.get(field, True if field == "enabled" else False), bool):
            errors[field] = f"{field} must be true or false"
    defaults = spec.get("default_models", {})
    if not isinstance(defaults, Mapping):
        errors["default_models"] = "expected an object keyed by model purpose"
        defaults = {}
    else:
        unknown_purposes = sorted(set(defaults) - {item.value for item in ModelPurpose})
        for purpose in unknown_purposes:
            errors[f"default_models.{purpose}"] = "unknown model purpose"
        for purpose, model in defaults.items():
            if purpose in unknown_purposes:
                continue
            if model is not None and (
                not isinstance(model, str)
                or not model.strip()
                or len(model.strip()) > 256
                or any(ord(character) < 32 for character in model)
            ):
                errors[f"default_models.{purpose}"] = (
                    "expected a model name up to 256 characters"
                )
    authentication = spec.get("authentication", {})
    try:
        parsed_authentication = _parse_authentication(authentication)
    except ProviderSettingsValidationError as exc:
        errors.update(exc.field_errors)
        parsed_authentication = None
    if (
        protocol is not None
        and isinstance(dialect, str)
        and parsed_authentication is not None
    ):
        _validate_auth_dialect(protocol, dialect, parsed_authentication, errors)
    if errors:
        raise ProviderSettingsValidationError(errors)
    return {
        "display_name": str(spec["display_name"]).strip(),
        "protocol": protocol,
        "dialect": dialect,
        "base_url": str(spec["base_url"]).strip(),
        "route_prefix": route_prefix.strip() or None
        if isinstance(route_prefix, str)
        else None,
        "authentication": parsed_authentication,
        "default_models": {
            str(purpose): str(model).strip()
            for purpose, model in defaults.items()
            if model is not None and str(model).strip()
        },
        "enabled": spec.get("enabled", True),
        "offline": spec.get("offline", False),
    }


def _parse_authentication(value: Any) -> ProviderAuthentication:
    if not isinstance(value, Mapping):
        raise ProviderSettingsValidationError({"authentication": "expected an object"})
    unknown = sorted(
        set(value)
        - {"ownership", "reference_kind", "reference_name", "service", "account"}
    )
    if unknown:
        raise ProviderSettingsValidationError(
            {
                f"authentication.{field}": "unknown authentication setting"
                for field in unknown
            }
        )
    try:
        ownership = AuthenticationOwnership(value.get("ownership", "secret_reference"))
    except (TypeError, ValueError) as exc:
        raise ProviderSettingsValidationError(
            {
                "authentication.ownership": "expected secret_reference, provider_native, or none"
            }
        ) from exc
    if ownership is not AuthenticationOwnership.SECRET_REFERENCE:
        if any(
            value.get(field)
            for field in ("reference_kind", "reference_name", "service", "account")
        ):
            raise ProviderSettingsValidationError(
                {
                    "authentication": "provider-native or unauthenticated profiles cannot retain a secret reference"
                }
            )
        return ProviderAuthentication(ownership)
    try:
        kind = SecretReferenceKind(value.get("reference_kind", "environment"))
    except (TypeError, ValueError) as exc:
        raise ProviderSettingsValidationError(
            {"authentication.reference_kind": "expected environment or keychain"}
        ) from exc
    if kind is SecretReferenceKind.TEST:
        raise ProviderSettingsValidationError(
            {"authentication.reference_kind": "test references cannot be persisted"}
        )
    name = value.get("reference_name")
    if not isinstance(name, str) or not name.strip():
        raise ProviderSettingsValidationError(
            {"authentication.reference_name": "secret reference name is required"}
        )
    try:
        reference = SecretReference(
            kind,
            name.strip(),
            service=_optional_text(value.get("service")),
            account=_optional_text(value.get("account")),
        )
    except ValueError as exc:
        raise ProviderSettingsValidationError(
            {"authentication.reference_name": str(exc)}
        ) from exc
    return ProviderAuthentication(ownership, reference)


def _validate_auth_dialect(
    protocol: ProviderProtocol,
    dialect: str,
    authentication: ProviderAuthentication,
    errors: dict[str, str],
) -> None:
    ownership = authentication.ownership
    if (
        protocol is ProviderProtocol.OPENAI_COMPATIBLE
        and ownership is AuthenticationOwnership.PROVIDER_NATIVE
    ):
        errors["authentication.ownership"] = (
            "OpenAI-compatible routes require a SecretRef or no authentication"
        )
    if (
        dialect == ANTHROPIC_MESSAGES_DIALECT
        and ownership is AuthenticationOwnership.PROVIDER_NATIVE
    ):
        errors["authentication.ownership"] = (
            "direct Anthropic routes require a SecretRef or no authentication"
        )
    if (
        dialect in {ANTHROPIC_BEDROCK_DIALECT, ANTHROPIC_VERTEX_DIALECT}
        and ownership is not AuthenticationOwnership.PROVIDER_NATIVE
    ):
        errors["authentication.ownership"] = (
            "this cloud platform requires provider-native authentication"
        )
    if (
        dialect == GEMINI_GENERATE_CONTENT_DIALECT
        and ownership is AuthenticationOwnership.PROVIDER_NATIVE
    ):
        errors["authentication.ownership"] = (
            "Gemini API routes require a SecretRef or no authentication"
        )


def _build_profile_and_routes(
    provider_id: str,
    spec: Mapping[str, Any],
) -> tuple[ProviderProfile, tuple[RouteProfile, ...]]:
    defaults = tuple(
        ModelPurposeDefault(ModelPurpose(purpose), model)
        for purpose, model in sorted(spec["default_models"].items())
    )
    common = {
        "provider_id": provider_id,
        "display_name": spec["display_name"],
        "base_url": spec["base_url"],
        "authentication": spec["authentication"],
        "ownership": ProviderOwnership.USER,
        "default_models": defaults,
        "offline": spec["offline"],
    }
    try:
        if spec["protocol"] is ProviderProtocol.OPENAI_COMPATIBLE:
            profile = custom_openai_compatible_profile(
                **common,
                route_prefix=spec["route_prefix"],
                wire_api=(
                    OpenAIWireAPI.RESPONSES
                    if spec["dialect"] == OPENAI_RESPONSES_DIALECT
                    else OpenAIWireAPI.CHAT_COMPLETIONS
                ),
            )
            route_builder = openai_compatible_route
        elif spec["protocol"] is ProviderProtocol.ANTHROPIC_COMPATIBLE:
            platform = {
                ANTHROPIC_MESSAGES_DIALECT: AnthropicPlatform.ANTHROPIC_API,
                ANTHROPIC_BEDROCK_DIALECT: AnthropicPlatform.AMAZON_BEDROCK,
                ANTHROPIC_VERTEX_DIALECT: AnthropicPlatform.GOOGLE_VERTEX,
                ANTHROPIC_FOUNDRY_DIALECT: AnthropicPlatform.MICROSOFT_FOUNDRY,
            }[spec["dialect"]]
            if platform is AnthropicPlatform.ANTHROPIC_API:
                profile = custom_anthropic_compatible_profile(
                    **common,
                    route_prefix=spec["route_prefix"],
                )
            else:
                profile = anthropic_cloud_profile(platform, **common)
            route_builder = anthropic_compatible_route
        else:
            if spec["dialect"] == GEMINI_VERTEX_DIALECT:
                profile = vertex_ai_gemini_profile(
                    **common,
                    route_prefix=spec["route_prefix"],
                )
            else:
                profile = custom_gemini_compatible_profile(
                    **common,
                    route_prefix=spec["route_prefix"],
                )
            route_builder = gemini_compatible_route
        routes = tuple(
            route_builder(
                profile,
                route_id=f"{provider_id}:{default.purpose.value}",
                model=default.model,
                purpose=default.purpose,
            )
            for default in defaults
        )
    except ValueError as exc:
        raise ProviderSettingsValidationError({"provider": str(exc)}) from exc
    return profile, routes


def _entry_spec(entry: ProviderRegistryEntry) -> dict[str, Any]:
    profile = entry.profile
    authentication = profile.authentication
    reference = authentication.secret_reference
    return {
        "display_name": profile.display_name,
        "protocol": profile.protocol.value,
        "dialect": profile.dialect,
        "base_url": profile.base_url,
        "route_prefix": profile.route_prefix,
        "authentication": {
            "ownership": authentication.ownership.value,
            "reference_kind": reference.kind.value if reference else None,
            "reference_name": reference.name if reference else None,
            "service": reference.service if reference else None,
            "account": reference.account if reference else None,
        },
        "default_models": {
            item.purpose.value: item.model for item in profile.default_models
        },
        "enabled": entry.enabled,
        "offline": profile.offline,
    }


def _authentication_projection(
    authentication: ProviderAuthentication,
) -> dict[str, Any]:
    reference = authentication.secret_reference
    kind = reference.kind.value if reference is not None else None
    return {
        "ownership": authentication.ownership.value,
        "reference_kind": kind,
        "reference_name": reference.name if reference is not None else None,
        "service": reference.service if reference is not None else None,
        "account": reference.account if reference is not None else None,
        "value_readable": False,
        "explanation": (
            f"The backend resolves the {kind} reference only at the owning probe or execution boundary; its value is never stored or returned."
            if reference is not None
            else "Authentication remains with the provider-native client."
            if authentication.ownership is AuthenticationOwnership.PROVIDER_NATIVE
            else "This endpoint is explicitly configured without authentication."
        ),
    }


def _health_projection(snapshot: ProviderHealthSnapshot) -> dict[str, Any]:
    return {
        "status": snapshot.status.value,
        "checked_at": snapshot.checked_at,
        "duration_ms": snapshot.duration_ms,
        "discovery_status": snapshot.discovery_status.value,
        "failure_kind": snapshot.failure_kind.value if snapshot.failure_kind else None,
        "reason_code": snapshot.reason_code,
        "discovery_reason_code": snapshot.discovery_reason_code,
        "cached": snapshot.cached,
        "models": [
            {"model": item.model, "source": item.source.value}
            for item in snapshot.models
        ],
    }


def _default_dialect(protocol: ProviderProtocol | None) -> str | None:
    return {
        ProviderProtocol.OPENAI_COMPATIBLE: OPENAI_RESPONSES_DIALECT,
        ProviderProtocol.ANTHROPIC_COMPATIBLE: ANTHROPIC_MESSAGES_DIALECT,
        ProviderProtocol.GEMINI_COMPATIBLE: GEMINI_GENERATE_CONTENT_DIALECT,
    }.get(protocol)


def _dialects(protocol: ProviderProtocol) -> frozenset[str]:
    return {
        ProviderProtocol.OPENAI_COMPATIBLE: frozenset(
            {OPENAI_RESPONSES_DIALECT, OPENAI_CHAT_COMPLETIONS_DIALECT}
        ),
        ProviderProtocol.ANTHROPIC_COMPATIBLE: frozenset(
            {
                ANTHROPIC_MESSAGES_DIALECT,
                ANTHROPIC_BEDROCK_DIALECT,
                ANTHROPIC_VERTEX_DIALECT,
                ANTHROPIC_FOUNDRY_DIALECT,
            }
        ),
        ProviderProtocol.GEMINI_COMPATIBLE: frozenset(
            {GEMINI_GENERATE_CONTENT_DIALECT, GEMINI_VERTEX_DIALECT}
        ),
    }[protocol]


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProviderSettingsValidationError(
            {"authentication": "reference metadata must be text or null"}
        )
    return value.strip() or None
