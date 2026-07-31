"""Serialization and validation helpers for provider registries."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable, Mapping

from gigaloom.execution import ProviderRef

from .profiles import (
    ProviderProfile,
    RouteProfile,
    provider_profile_from_dict,
    provider_profile_to_dict,
    route_profile_from_dict,
    route_profile_to_dict,
)
from .registry import (
    MAX_PROVIDER_MODEL_CHARS,
    MAX_PROVIDER_MODELS,
    MAX_PROVIDER_REASON_CHARS,
    ProviderRegistryConflict,
    ProviderRegistryEntry,
)
from .health import (
    ProviderDiscoveryStatus,
    ProviderFailureKind,
    ProviderHealthSnapshot,
    ProviderHealthStatus,
    ProviderModelEvidence,
    ProviderModelSource,
)


def _entry_to_dict(entry: ProviderRegistryEntry) -> dict[str, Any]:
    return {
        "profile": provider_profile_to_dict(entry.profile),
        "routes": [route_profile_to_dict(item) for item in entry.routes],
        "enabled": entry.enabled,
        "revision": entry.revision,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _entry_from_dict(data: Any) -> ProviderRegistryEntry:
    if not isinstance(data, Mapping):
        raise ValueError("provider registry entry must be an object")
    allowed = {
        "profile",
        "routes",
        "enabled",
        "revision",
        "created_at",
        "updated_at",
    }
    if set(data) != allowed:
        raise ValueError("provider registry entry fields are invalid")
    raw_routes = data.get("routes")
    if not isinstance(raw_routes, list):
        raise ValueError("provider registry routes must be a list")
    enabled = data.get("enabled")
    revision = data.get("revision")
    if not isinstance(enabled, bool):
        raise ValueError("provider registry enabled must be a boolean")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("provider registry revision must be an integer")
    return ProviderRegistryEntry(
        profile=provider_profile_from_dict(_mapping(data.get("profile"))),
        routes=tuple(route_profile_from_dict(_mapping(item)) for item in raw_routes),
        enabled=enabled,
        revision=revision,
        created_at=_required_text(data.get("created_at"), "created_at"),
        updated_at=_required_text(data.get("updated_at"), "updated_at"),
    )


def _health_to_dict(snapshot: ProviderHealthSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "provider": {
            "id": snapshot.provider.id,
            "revision": snapshot.provider.revision,
        },
        "status": snapshot.status.value,
        "checked_at": snapshot.checked_at,
        "duration_ms": snapshot.duration_ms,
        "discovery_status": snapshot.discovery_status.value,
        "models": [
            {"model": item.model, "source": item.source.value}
            for item in snapshot.models
        ],
        "failure_kind": (
            snapshot.failure_kind.value if snapshot.failure_kind is not None else None
        ),
        "reason_code": snapshot.reason_code,
        "discovery_reason_code": snapshot.discovery_reason_code,
    }


def _health_from_dict(data: Any) -> ProviderHealthSnapshot:
    if not isinstance(data, Mapping):
        raise ValueError("provider health snapshot must be an object")
    allowed = {
        "schema_version",
        "provider",
        "status",
        "checked_at",
        "duration_ms",
        "discovery_status",
        "models",
        "failure_kind",
        "reason_code",
        "discovery_reason_code",
    }
    if set(data) != allowed:
        raise ValueError("provider health snapshot fields are invalid")
    raw_provider = _mapping(data.get("provider"))
    if set(raw_provider) != {"id", "revision"}:
        raise ValueError("provider health reference fields are invalid")
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise ValueError("provider health models must be a list")
    models = []
    for raw_model in raw_models:
        model = _mapping(raw_model)
        if set(model) != {"model", "source"}:
            raise ValueError("provider health model fields are invalid")
        models.append(
            ProviderModelEvidence(
                _required_text(model.get("model"), "model"),
                ProviderModelSource(_required_text(model.get("source"), "source")),
            )
        )
    raw_failure = data.get("failure_kind")
    return ProviderHealthSnapshot(
        provider=ProviderRef(
            _required_text(raw_provider.get("id"), "provider id"),
            _required_text(raw_provider.get("revision"), "provider revision"),
        ),
        status=ProviderHealthStatus(_required_text(data.get("status"), "status")),
        checked_at=_required_text(data.get("checked_at"), "checked_at"),
        duration_ms=_required_int(data.get("duration_ms"), "duration_ms"),
        discovery_status=ProviderDiscoveryStatus(
            _required_text(data.get("discovery_status"), "discovery_status")
        ),
        models=tuple(models),
        failure_kind=(
            ProviderFailureKind(raw_failure) if isinstance(raw_failure, str) else None
        ),
        reason_code=_optional_text(data.get("reason_code")),
        discovery_reason_code=_optional_text(data.get("discovery_reason_code")),
        schema_version=_required_int(data.get("schema_version"), "schema_version"),
    )


def _validate_route_binding(profile: ProviderProfile, route: RouteProfile) -> None:
    if not isinstance(route, RouteProfile):
        raise ValueError("provider registry route is invalid")
    if route.provider != profile.ref:
        raise ValueError("provider registry route has a stale provider reference")
    if route.protocol is not profile.protocol or route.dialect != profile.dialect:
        raise ValueError("provider registry route protocol does not match provider")
    if route.effective_base_url != profile.effective_base_url:
        raise ValueError("provider registry route endpoint does not match provider")
    if route.authentication_ownership is not profile.authentication.ownership:
        raise ValueError(
            "provider registry route authentication does not match provider"
        )


def _require_current(
    entries: Mapping[str, ProviderRegistryEntry],
    provider_id: str,
    expected_revision: int,
) -> ProviderRegistryEntry:
    _validate_identity(provider_id, field_name="provider id")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected provider revision must be an integer")
    current = entries.get(provider_id)
    if current is None:
        raise ProviderRegistryConflict("provider does not exist")
    if current.revision != expected_revision:
        raise ProviderRegistryConflict("provider registry revision changed")
    return current


def _validate_replacement_revisions(
    current: ProviderRegistryEntry,
    profile: ProviderProfile,
    routes: tuple[RouteProfile, ...],
) -> None:
    current_profile = provider_profile_to_dict(current.profile)
    incoming_profile = provider_profile_to_dict(profile)
    current_profile.pop("revision")
    incoming_profile.pop("revision")
    if (
        current_profile != incoming_profile
        and current.profile.revision == profile.revision
    ):
        raise ProviderRegistryConflict(
            "changed provider content requires a new profile revision"
        )
    current_routes = {item.id: item for item in current.routes}
    for route in routes:
        prior = current_routes.get(route.id)
        if prior is None:
            continue
        prior_payload = route_profile_to_dict(prior)
        incoming_payload = route_profile_to_dict(route)
        prior_payload.pop("revision")
        incoming_payload.pop("revision")
        if prior_payload != incoming_payload and prior.revision == route.revision:
            raise ProviderRegistryConflict(
                "changed route content requires a new route revision"
            )


def _merge_model_evidence(
    profile: ProviderProfile,
    discovered: Iterable[str],
) -> tuple[ProviderModelEvidence, ...]:
    discovered_names = _normalize_discovered_model_names(discovered)
    result = [
        ProviderModelEvidence(item, ProviderModelSource.DISCOVERED)
        for item in discovered_names
    ]
    seen = set(discovered_names)
    for default in profile.default_models:
        if default.model not in seen:
            result.append(
                ProviderModelEvidence(
                    default.model,
                    ProviderModelSource.CONFIGURED_FALLBACK,
                )
            )
            seen.add(default.model)
    return _normalize_models(result)


def _normalize_models(
    models: Iterable[ProviderModelEvidence],
) -> tuple[ProviderModelEvidence, ...]:
    values = tuple(models)
    if len(values) > MAX_PROVIDER_MODELS:
        raise ValueError("provider model evidence exceeds the bounded limit")
    if any(not isinstance(item, ProviderModelEvidence) for item in values):
        raise ValueError("provider model evidence is invalid")
    if len({item.model for item in values}) != len(values):
        raise ValueError("provider model evidence contains duplicate names")
    return tuple(sorted(values, key=lambda item: (item.model, item.source.value)))


def _normalize_discovered_model_names(models: Iterable[str]) -> tuple[str, ...]:
    values = tuple(models)
    if len(values) > MAX_PROVIDER_MODELS:
        raise ValueError("provider discovery exceeds the bounded model limit")
    normalized = []
    seen = set()
    for item in values:
        _validate_model(item)
        if item not in seen:
            normalized.append(item)
            seen.add(item)
    return tuple(sorted(normalized))


def _validate_model(model: str) -> None:
    if not isinstance(model, str) or not model.strip():
        raise ValueError("provider model must be non-empty")
    if len(model) > MAX_PROVIDER_MODEL_CHARS or any(ord(char) < 32 for char in model):
        raise ValueError("provider model is invalid")


def _validate_identity(value: str, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(not (char.isalnum() or char in "._:/@+~-") for char in value)
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_reason(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PROVIDER_REASON_CHARS
        or any(not (char.isalnum() or char in "._:-") for char in value)
    ):
        raise ValueError("provider reason code is invalid")


def _validate_optional_reason(value: str | None) -> None:
    if value is not None:
        _validate_reason(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("provider persisted value must be an object")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"provider {field_name} must be non-empty")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("provider persisted text is invalid")
    return value


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"provider {field_name} must be an integer")
    return value


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("provider timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("provider timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("provider timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _duration_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))


def _hashed_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
