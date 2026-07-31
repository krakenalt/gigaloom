"""Built-in provider compatibility and profile validation helpers."""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from gigaloom.execution import ExecutionTransport, SnapshotEvidenceRef
from gigaloom.secrets import (
    secret_reference_from_dict,
    secret_reference_to_dict,
)

from .profiles import (
    _IDENTITY_RE,
    AdapterProtocolCompatibility,
    AuthenticationOwnership,
    ModelPurpose,
    ModelPurposeDefault,
    ProviderAuthentication,
    ProviderProfile,
    ProviderProtocol,
    RouteCompatibilityError,
    RouteProfile,
)


def direct_chat_legacy_compatibility() -> AdapterProtocolCompatibility:
    """Return reviewed Direct Chat compatibility with legacy proxy routes."""
    return _legacy_compatibility(
        harness_id="direct-chat",
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        transports=(ExecutionTransport.ONE_SHOT,),
        capabilities=("chat", "streaming", "tools"),
        native_auth=False,
    )


def codex_legacy_compatibility() -> AdapterProtocolCompatibility:
    """Return reviewed Codex compatibility with legacy proxy routes."""
    return _legacy_compatibility(
        harness_id="codex-cli",
        protocol=ProviderProtocol.OPENAI_COMPATIBLE,
        transports=(
            ExecutionTransport.NATIVE_STRUCTURED,
            ExecutionTransport.NATIVE_TERMINAL,
            ExecutionTransport.ONE_SHOT,
        ),
        capabilities=("chat", "streaming", "tools"),
        native_auth=False,
    )


def claude_legacy_compatibility() -> AdapterProtocolCompatibility:
    """Return bounded Claude compatibility without embedded structured claims."""
    return _legacy_compatibility(
        harness_id="claude-code",
        protocol=ProviderProtocol.ANTHROPIC_COMPATIBLE,
        transports=(
            ExecutionTransport.NATIVE_TERMINAL,
            ExecutionTransport.ONE_SHOT,
        ),
        capabilities=("chat", "streaming", "tools"),
        native_auth=False,
    )


def gemini_legacy_compatibility() -> AdapterProtocolCompatibility:
    """Return reviewed Gemini compatibility including ACP native auth evidence."""
    return _legacy_compatibility(
        harness_id="gemini-cli",
        protocol=ProviderProtocol.GEMINI_COMPATIBLE,
        transports=(
            ExecutionTransport.NATIVE_STRUCTURED,
            ExecutionTransport.NATIVE_TERMINAL,
            ExecutionTransport.ONE_SHOT,
        ),
        capabilities=("chat", "streaming", "tools"),
        native_auth=True,
    )


def _legacy_compatibility(
    *,
    harness_id: str,
    protocol: ProviderProtocol,
    transports: tuple[ExecutionTransport, ...],
    capabilities: tuple[str, ...],
    native_auth: bool,
) -> AdapterProtocolCompatibility:
    adapter_version = _adapter_version()
    semantic = f"{harness_id}:{adapter_version}:{protocol.value}:legacy-v1-v2"
    revision = hashlib.sha256(semantic.encode("utf-8")).hexdigest()
    return AdapterProtocolCompatibility(
        id=f"legacy-gpt2giga-{harness_id}",
        revision=revision,
        harness_id=harness_id,
        adapter_version=adapter_version,
        protocol=protocol,
        dialects=("gpt2giga-v1", "gpt2giga-v2"),
        transports=transports,
        capabilities=capabilities,
        native_auth=native_auth,
        evidence=(
            SnapshotEvidenceRef(
                id=f"legacy-{harness_id}-compatibility",
                revision=revision,
                status="supported",
                source="built-in-contract",
            ),
        ),
    )


_BUILTIN_COMPATIBILITY_FACTORIES = (
    direct_chat_legacy_compatibility,
    codex_legacy_compatibility,
    claude_legacy_compatibility,
    gemini_legacy_compatibility,
)
for _factory in _BUILTIN_COMPATIBILITY_FACTORIES:
    _factory.__module__ = "gigaloom.provider_profiles"
_LEGACY_PROTOCOLS = {
    "direct-chat": ProviderProtocol.OPENAI_COMPATIBLE,
    "codex-cli": ProviderProtocol.OPENAI_COMPATIBLE,
    "claude-code": ProviderProtocol.ANTHROPIC_COMPATIBLE,
    "gemini-cli": ProviderProtocol.GEMINI_COMPATIBLE,
}
_LEGACY_CAPABILITIES = {
    "direct-chat": ("chat", "streaming", "tools"),
    "codex-cli": ("chat", "streaming", "tools"),
    "claude-code": ("chat", "streaming", "tools"),
    "gemini-cli": ("chat", "streaming", "tools"),
}


def _validate_profile_route(
    provider: ProviderProfile,
    route: RouteProfile,
) -> None:
    if route.provider != provider.ref:
        raise RouteCompatibilityError(
            "provider_revision_mismatch",
            "route does not reference the selected provider revision",
        )
    if route.protocol is not provider.protocol:
        raise RouteCompatibilityError(
            "protocol_mismatch",
            "route protocol does not match its provider",
        )
    if route.dialect != provider.dialect:
        raise RouteCompatibilityError(
            "dialect_mismatch",
            "route dialect does not match its provider",
        )
    if route.effective_base_url != provider.effective_base_url:
        raise RouteCompatibilityError(
            "endpoint_mismatch",
            "route endpoint does not match its provider revision",
        )
    if route.authentication_ownership is not provider.authentication.ownership:
        raise RouteCompatibilityError(
            "authentication_mismatch",
            "route authentication ownership does not match its provider",
        )
    defaults = {item.purpose: item.model for item in provider.default_models}
    if route.purpose in defaults and defaults[route.purpose] != route.model:
        raise RouteCompatibilityError(
            "model_purpose_mismatch",
            "route model contradicts the provider purpose default",
        )


def _supported_capabilities(
    evidence: Iterable[SnapshotEvidenceRef],
) -> set[str]:
    return {item.id for item in evidence if item.status == "supported"}


def _normalize_model_defaults(
    defaults: Iterable[ModelPurposeDefault],
) -> tuple[ModelPurposeDefault, ...]:
    values = tuple(defaults)
    if any(not isinstance(item, ModelPurposeDefault) for item in values):
        raise ValueError("provider model defaults are invalid")
    normalized = tuple(sorted(values, key=lambda item: item.purpose.value))
    if len({item.purpose for item in normalized}) != len(normalized):
        raise ValueError("provider model defaults contain duplicate purposes")
    return normalized


def _normalize_evidence(
    evidence: Iterable[SnapshotEvidenceRef],
) -> tuple[SnapshotEvidenceRef, ...]:
    values = tuple(evidence)
    if any(not isinstance(item, SnapshotEvidenceRef) for item in values):
        raise ValueError("capability evidence is invalid")
    normalized = tuple(sorted(values, key=lambda item: (item.id, item.revision)))
    if len({item.id for item in normalized}) != len(normalized):
        raise ValueError("capability evidence contains duplicate ids")
    return normalized


def _normalize_identities(
    values: Iterable[str],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} is required")
    for value in normalized:
        _validate_identity(value, field_name=field_name)
    return normalized


def _canonical_base_url(value: str) -> str:
    _validate_text(value, field_name="base URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL cannot contain credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if any(part == ".." for part in path.split("/")):
        raise ValueError("base URL path cannot traverse parents")
    netloc = parsed.netloc.lower()
    return urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


def _canonical_route_prefix(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not text.startswith("/") or "?" in text or "#" in text:
        raise ValueError("route prefix must be an absolute URL path")
    normalized = "/" + text.strip("/")
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError("route prefix cannot traverse parents")
    return normalized


def _authentication_to_dict(value: ProviderAuthentication) -> dict[str, Any]:
    return {
        "ownership": value.ownership.value,
        "secret_reference": (
            secret_reference_to_dict(value.secret_reference)
            if value.secret_reference is not None
            else None
        ),
    }


def _authentication_from_dict(value: Any) -> ProviderAuthentication:
    mapping = _strict_mapping(
        value,
        allowed={"ownership", "secret_reference"},
        field_name="provider authentication",
    )
    raw_reference = mapping.get("secret_reference")
    return ProviderAuthentication(
        ownership=_enum_value(
            AuthenticationOwnership,
            mapping.get("ownership"),
            field_name="authentication ownership",
        ),
        secret_reference=(
            secret_reference_from_dict(raw_reference)
            if isinstance(raw_reference, Mapping)
            else None
        ),
    )


def _evidence_to_dict(value: SnapshotEvidenceRef) -> dict[str, str]:
    return {
        "id": value.id,
        "revision": value.revision,
        "status": value.status,
        "source": value.source,
    }


def _evidence_from_list(
    value: Any, *, field_name: str
) -> tuple[SnapshotEvidenceRef, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    parsed: list[SnapshotEvidenceRef] = []
    for item in value:
        mapping = _strict_mapping(
            item,
            allowed={"id", "revision", "status", "source"},
            field_name=field_name,
        )
        parsed.append(
            SnapshotEvidenceRef(
                id=_required_text(mapping.get("id"), field_name="evidence id"),
                revision=_required_text(
                    mapping.get("revision"), field_name="evidence revision"
                ),
                status=_required_text(
                    mapping.get("status"), field_name="evidence status"
                ),
                source=_required_text(
                    mapping.get("source"), field_name="evidence source"
                ),
            )
        )
    return tuple(parsed)


def _model_defaults_from_list(value: Any) -> tuple[ModelPurposeDefault, ...]:
    if not isinstance(value, list):
        raise ValueError("provider default_models must be a list")
    parsed: list[ModelPurposeDefault] = []
    for item in value:
        mapping = _strict_mapping(
            item,
            allowed={"purpose", "model"},
            field_name="provider model default",
        )
        parsed.append(
            ModelPurposeDefault(
                purpose=_enum_value(
                    ModelPurpose,
                    mapping.get("purpose"),
                    field_name="model default purpose",
                ),
                model=_required_text(
                    mapping.get("model"), field_name="model default model"
                ),
            )
        )
    return tuple(parsed)


def _legacy_protocol(harness_id: str) -> ProviderProtocol:
    _validate_identity(harness_id, field_name="legacy harness id")
    try:
        return _LEGACY_PROTOCOLS[harness_id]
    except KeyError as exc:
        raise ValueError("legacy harness has no reviewed provider protocol") from exc


def _adapter_version() -> str:
    try:
        value = metadata.version("gigaloom")
    except metadata.PackageNotFoundError:
        return "source"
    return value.strip() or "source"


def _compatibility_identity(value: AdapterProtocolCompatibility) -> str:
    payload = {
        "id": value.id,
        "revision": value.revision,
        "harness_id": value.harness_id,
        "adapter_version": value.adapter_version,
        "protocol": value.protocol.value,
        "dialects": value.dialects,
        "transports": tuple(item.value for item in value.transports),
        "capabilities": value.capabilities,
        "native_auth": value.native_auth,
        "evidence": tuple(_evidence_to_dict(item) for item in value.evidence),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _select_entry_points(all_entry_points, group: str):
    if hasattr(all_entry_points, "select"):
        return all_entry_points.select(group=group)
    return all_entry_points.get(group, ())


def _entry_point_sort_key(entry_point) -> tuple[str, str]:
    return (
        str(getattr(entry_point, "name", "")),
        str(getattr(entry_point, "value", "")),
    )


def _entry_point_identity(entry_point, loaded) -> str:
    value = getattr(entry_point, "value", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _implementation_identity(loaded)


def _implementation_identity(implementation) -> str:
    module = getattr(implementation, "__module__", type(implementation).__module__)
    qualname = getattr(
        implementation,
        "__qualname__",
        type(implementation).__qualname__,
    )
    return f"{module}:{qualname}"


def _load_entry_point_compatibility(loaded) -> AdapterProtocolCompatibility:
    value = loaded() if callable(loaded) else loaded
    if not isinstance(value, AdapterProtocolCompatibility):
        raise TypeError("provider entry point did not create compatibility evidence")
    return value


def _strict_mapping(
    value: Any,
    *,
    allowed: set[str],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unknown {field_name} fields: {sorted(unknown)}")
    return value


def _enum_value(enum_type, value: Any, *, field_name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text value is invalid")
    return value.strip() or None


def _required_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _required_int(value: Any, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _validate_identity(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _validate_text(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _validate_non_negative_int(value: int, *, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
