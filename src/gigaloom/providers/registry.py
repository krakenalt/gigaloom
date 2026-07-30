"""Durable provider registry and bounded provider health checks."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Iterable, Mapping

from gigaloom.providers.profiles import (
    ProviderOwnership,
    ProviderProfile,
    RouteProfile,
)
from gigaloom.sessions.contracts import exclusive_file_lock


PROVIDER_REGISTRY_SCHEMA_VERSION = 1
PROVIDER_HEALTH_SCHEMA_VERSION = 1
MAX_PROVIDER_MODELS = 500
MAX_PROVIDER_MODEL_CHARS = 256
MAX_PROVIDER_REASON_CHARS = 128
MAX_PROVIDER_CHECK_TIMEOUT_SECONDS = 30.0

PROVIDER_SOURCE_PRECEDENCE = (
    ProviderOwnership.MANAGED_POLICY,
    ProviderOwnership.ENVIRONMENT,
    ProviderOwnership.PROJECT,
    ProviderOwnership.USER,
    ProviderOwnership.MIGRATED_LEGACY,
    ProviderOwnership.BUILT_IN,
)


class ProviderRegistryConflict(RuntimeError):
    """Raised when provider registry state changed before a mutation."""


ProviderRegistryConflict.__module__ = "gigaloom.provider_registry"


class ProviderRegistryOwnershipError(RuntimeError):
    """Raised when a mutation crosses its configured source owner."""


@dataclass(frozen=True)
class ProviderRegistryEntry:
    """One persisted provider, its routes, and its optimistic revision."""

    profile: ProviderProfile
    routes: tuple[RouteProfile, ...]
    enabled: bool
    revision: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ProviderProfile):
            raise ValueError("provider registry profile is invalid")
        routes = tuple(self.routes)
        if len({item.id for item in routes}) != len(routes):
            raise ValueError("provider registry route ids must be unique")
        for route in routes:
            _validate_route_binding(self.profile, route)
        object.__setattr__(
            self, "routes", tuple(sorted(routes, key=lambda item: item.id))
        )
        if not isinstance(self.enabled, bool):
            raise ValueError("provider registry enabled must be a boolean")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ValueError("provider registry revision must be positive")
        _parse_timestamp(self.created_at)
        _parse_timestamp(self.updated_at)


@dataclass(frozen=True)
class EffectiveProvider:
    """One effective provider plus lower-precedence sources it shadows."""

    entry: ProviderRegistryEntry
    source: ProviderOwnership
    shadowed_sources: tuple[ProviderOwnership, ...] = ()


class ProviderRegistryStore:
    """Atomically persist one provider ownership layer with stale-write checks."""

    def __init__(
        self,
        data_dir: str | Path,
        ownership: ProviderOwnership,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(ownership, ProviderOwnership):
            raise ValueError("provider registry ownership is invalid")
        self.ownership = ownership
        self.root = Path(data_dir).expanduser().resolve() / "providers"
        self.path = self.root / f"{ownership.value}.json"
        self.lock_path = self.root / f".{ownership.value}.lock"
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list(self) -> tuple[ProviderRegistryEntry, ...]:
        """Return the complete source layer in deterministic order."""
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
        return tuple(sorted(entries.values(), key=lambda item: item.profile.id))

    def get(self, provider_id: str) -> ProviderRegistryEntry | None:
        """Return one provider entry when it exists in this source layer."""
        _validate_identity(provider_id, field_name="provider id")
        with exclusive_file_lock(self.lock_path):
            return self._read_unlocked().get(provider_id)

    def create(
        self,
        profile: ProviderProfile,
        *,
        routes: Iterable[RouteProfile] = (),
        enabled: bool = True,
    ) -> ProviderRegistryEntry:
        """Create a provider without replacing an existing source identity."""
        self._require_ownership(profile)
        timestamp = _format_timestamp(self._now())
        entry = ProviderRegistryEntry(
            profile=profile,
            routes=tuple(routes),
            enabled=enabled,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
            if profile.id in entries:
                raise ProviderRegistryConflict("provider already exists")
            entries[profile.id] = entry
            self._write_unlocked(entries)
        return entry

    def replace(
        self,
        profile: ProviderProfile,
        *,
        routes: Iterable[RouteProfile],
        enabled: bool,
        expected_revision: int,
    ) -> ProviderRegistryEntry:
        """Replace one provider only when its store revision is current."""
        self._require_ownership(profile)
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
            current = _require_current(entries, profile.id, expected_revision)
            normalized_routes = tuple(routes)
            _validate_replacement_revisions(current, profile, normalized_routes)
            updated = ProviderRegistryEntry(
                profile=profile,
                routes=normalized_routes,
                enabled=enabled,
                revision=current.revision + 1,
                created_at=current.created_at,
                updated_at=_format_timestamp(self._now()),
            )
            entries[profile.id] = updated
            self._write_unlocked(entries)
        return updated

    def set_enabled(
        self,
        provider_id: str,
        enabled: bool,
        *,
        expected_revision: int,
    ) -> ProviderRegistryEntry:
        """Enable or disable one provider with optimistic concurrency."""
        if not isinstance(enabled, bool):
            raise ValueError("provider enabled must be a boolean")
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
            current = _require_current(entries, provider_id, expected_revision)
            updated = replace(
                current,
                enabled=enabled,
                revision=current.revision + 1,
                updated_at=_format_timestamp(self._now()),
            )
            entries[provider_id] = updated
            self._write_unlocked(entries)
        return updated

    def delete(self, provider_id: str, *, expected_revision: int) -> None:
        """Delete one provider only when the caller observed its latest revision."""
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
            _require_current(entries, provider_id, expected_revision)
            del entries[provider_id]
            self._write_unlocked(entries)

    def clone(
        self,
        source_id: str,
        profile: ProviderProfile,
        *,
        routes: Iterable[RouteProfile],
        expected_source_revision: int,
        enabled: bool = True,
    ) -> ProviderRegistryEntry:
        """Clone reviewed provider data into a new, caller-supplied identity."""
        self._require_ownership(profile)
        if source_id == profile.id:
            raise ValueError("cloned provider id must differ from its source")
        timestamp = _format_timestamp(self._now())
        cloned = ProviderRegistryEntry(
            profile=profile,
            routes=tuple(routes),
            enabled=enabled,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with exclusive_file_lock(self.lock_path):
            entries = self._read_unlocked()
            _require_current(entries, source_id, expected_source_revision)
            if profile.id in entries:
                raise ProviderRegistryConflict("cloned provider already exists")
            entries[profile.id] = cloned
            self._write_unlocked(entries)
        return cloned

    def initialize(self, entries: Iterable[ProviderRegistryEntry]) -> None:
        """Atomically initialize an empty ownership layer from reviewed entries."""
        normalized = tuple(entries)
        indexed: dict[str, ProviderRegistryEntry] = {}
        for entry in normalized:
            if not isinstance(entry, ProviderRegistryEntry):
                raise TypeError("provider registry initialization entry is invalid")
            self._require_ownership(entry.profile)
            if entry.profile.id in indexed:
                raise ValueError("provider registry initialization ids must be unique")
            indexed[entry.profile.id] = entry
        with exclusive_file_lock(self.lock_path):
            if self.path.exists():
                raise ProviderRegistryConflict("provider registry already exists")
            self._write_unlocked(indexed)

    def _require_ownership(self, profile: ProviderProfile) -> None:
        if profile.ownership is not self.ownership:
            raise ProviderRegistryOwnershipError(
                "provider ownership does not match registry layer"
            )

    def _read_unlocked(self) -> dict[str, ProviderRegistryEntry]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("provider registry is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("provider registry must be an object")
        if set(payload) != {"schema_version", "ownership", "providers"}:
            raise ValueError("provider registry fields are invalid")
        if payload.get("schema_version") != PROVIDER_REGISTRY_SCHEMA_VERSION:
            raise ValueError("unsupported provider registry schema_version")
        if payload.get("ownership") != self.ownership.value:
            raise ProviderRegistryOwnershipError("provider registry owner changed")
        raw_entries = payload.get("providers")
        if not isinstance(raw_entries, list):
            raise ValueError("provider registry providers must be a list")
        entries: dict[str, ProviderRegistryEntry] = {}
        for raw_entry in raw_entries:
            entry = _entry_from_dict(raw_entry)
            self._require_ownership(entry.profile)
            if entry.profile.id in entries:
                raise ValueError("provider registry contains duplicate ids")
            entries[entry.profile.id] = entry
        return entries

    def _write_unlocked(self, entries: Mapping[str, ProviderRegistryEntry]) -> None:
        payload = {
            "schema_version": PROVIDER_REGISTRY_SCHEMA_VERSION,
            "ownership": self.ownership.value,
            "providers": [
                _entry_to_dict(entries[provider_id]) for provider_id in sorted(entries)
            ],
        }
        _atomic_private_json(self.path, payload)


class LayeredProviderRegistry:
    """Resolve provider ids across explicit ownership layers."""

    def __init__(
        self,
        sources: Mapping[ProviderOwnership, Iterable[ProviderRegistryEntry]],
    ) -> None:
        normalized: dict[ProviderOwnership, tuple[ProviderRegistryEntry, ...]] = {}
        for ownership, entries in sources.items():
            if not isinstance(ownership, ProviderOwnership):
                raise ValueError("provider source ownership is invalid")
            values = tuple(entries)
            if any(item.profile.ownership is not ownership for item in values):
                raise ProviderRegistryOwnershipError(
                    "provider source contains a foreign ownership entry"
                )
            if len({item.profile.id for item in values}) != len(values):
                raise ValueError("provider source contains duplicate ids")
            normalized[ownership] = values
        self._sources = normalized

    def list(self) -> tuple[EffectiveProvider, ...]:
        """Return effective providers using stable source precedence."""
        candidates: dict[
            str, list[tuple[ProviderOwnership, ProviderRegistryEntry]]
        ] = {}
        for ownership in PROVIDER_SOURCE_PRECEDENCE:
            for entry in self._sources.get(ownership, ()):
                candidates.setdefault(entry.profile.id, []).append((ownership, entry))
        effective = []
        for provider_id in sorted(candidates):
            values = candidates[provider_id]
            source, entry = values[0]
            effective.append(
                EffectiveProvider(
                    entry=entry,
                    source=source,
                    shadowed_sources=tuple(item[0] for item in values[1:]),
                )
            )
        return tuple(effective)

    def get(self, provider_id: str) -> EffectiveProvider | None:
        """Return one effective provider, including a disabled upper layer."""
        _validate_identity(provider_id, field_name="provider id")
        return next(
            (item for item in self.list() if item.entry.profile.id == provider_id),
            None,
        )


from .health import (  # noqa: E402, F401
    ProviderAuthenticationFailure,
    ProviderCompatibilityFailure,
    ProviderDiscoveryStatus,
    ProviderFailureKind,
    ProviderHealthFailure,
    ProviderHealthService,
    ProviderHealthSnapshot,
    ProviderHealthStatus,
    ProviderHealthStore,
    ProviderModelEvidence,
    ProviderModelSource,
    ProviderNetworkPolicyDecision,
    ProviderProbeBackend,
    ProviderProbeFailure,
    ProviderProbeRequest,
    ProviderProbeResponse,
    ProviderTransportFailure,
)
from .registry_codec import (  # noqa: E402
    _atomic_private_json,
    _entry_from_dict,
    _entry_to_dict,
    _format_timestamp,
    _parse_timestamp,
    _require_current,
    _validate_identity,
    _validate_replacement_revisions,
    _validate_route_binding,
)
