"""Backend-authoritative provider Settings application service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gpt2giga_harness.providers.protocols.anthropic.compatible import (
    ANTHROPIC_MESSAGES_DIALECT,
)
from gpt2giga_harness.providers.protocols.gemini.compatible import (
    GEMINI_GENERATE_CONTENT_DIALECT,
    GEMINI_VERTEX_DIALECT,
)
from gpt2giga_harness.providers.protocols.openai.compatible import (
    OPENAI_CHAT_COMPLETIONS_DIALECT,
    OPENAI_RESPONSES_DIALECT,
)
from gpt2giga_harness.providers.profiles import (
    ProviderCompatibilityRegistry,
    ProviderOwnership,
    ProviderProtocol,
)
from gpt2giga_harness.providers.migration import provider_migration_aliases
from gpt2giga_harness.providers.registry import (
    LayeredProviderRegistry,
    ProviderCompatibilityFailure,
    ProviderHealthService,
    ProviderHealthStore,
    ProviderProbeBackend,
    ProviderProbeRequest,
    ProviderRegistryConflict,  # noqa: F401
    ProviderRegistryEntry,
    ProviderRegistryStore,
)


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


class ProviderSettingsValidationError(ValueError):
    """Field-level provider Settings validation failure."""

    def __init__(self, field_errors: Mapping[str, str]) -> None:
        self.field_errors = dict(field_errors)
        super().__init__(
            "; ".join(f"{key}: {value}" for key, value in self.field_errors.items())
        )


class ProviderSettingsNotFoundError(KeyError):
    """Raised when a user-owned provider does not exist."""


ProviderSettingsNotFoundError.__module__ = "gpt2giga_harness.provider_settings"


@dataclass(frozen=True)
class ProviderSettingsMutation:
    """Authoritative read-back projection after one provider mutation."""

    provider: dict[str, Any]
    effects: Mapping[str, str]


class _UnavailableProbeBackend:
    def check(self, request: ProviderProbeRequest):
        del request
        raise ProviderCompatibilityFailure("probe_backend_unavailable")


class ProviderSettingsService:
    """Own user provider CRUD, safe projections, compatibility, and probes."""

    def __init__(
        self,
        data_dir: str,
        *,
        probe_backends: Mapping[ProviderProtocol, ProviderProbeBackend] | None = None,
        compatibility_registry: ProviderCompatibilityRegistry | None = None,
    ) -> None:
        self.store = ProviderRegistryStore(data_dir, ProviderOwnership.USER)
        self.migrated_store = ProviderRegistryStore(
            data_dir, ProviderOwnership.MIGRATED_LEGACY
        )
        self.health_store = ProviderHealthStore(data_dir)
        self.compatibility = (
            compatibility_registry or ProviderCompatibilityRegistry.with_builtins()
        )
        backends = dict(probe_backends or {})
        self.health_services = {
            protocol: ProviderHealthService(
                backends.get(protocol, _UnavailableProbeBackend()),
                self.health_store,
            )
            for protocol in ProviderProtocol
        }

    def list(self) -> dict[str, Any]:
        """Return a bounded reference-only registry projection."""
        entries = self._effective_entries()
        return {
            "providers": [self._entry_projection(item) for item in entries],
            "templates": [dict(item) for item in PROVIDER_TEMPLATES],
            "effects": dict(PROVIDER_EFFECTS),
            "secret_contract": {
                "accepted_reference_kinds": ["environment", "keychain"],
                "values_accepted": False,
                "values_returned": False,
                "filesystem_paths_accepted": False,
            },
            "discovery_errors": list(self.compatibility.discovery_errors),
            "compatibility_aliases": provider_migration_aliases(),
        }

    def get(self, provider_id: str) -> dict[str, Any]:
        """Return one user-owned provider projection."""
        return self._entry_projection(self._require(provider_id))

    def create(
        self, provider_id: str, payload: Mapping[str, Any]
    ) -> ProviderSettingsMutation:
        """Validate, create, and read back one provider."""
        spec = _normalize_spec(provider_id, payload, current=None)
        profile, routes = _build_profile_and_routes(provider_id, spec)
        entry = self.store.create(profile, routes=routes, enabled=spec["enabled"])
        return ProviderSettingsMutation(self._entry_projection(entry), PROVIDER_EFFECTS)

    def update(
        self,
        provider_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> ProviderSettingsMutation:
        """Validate, optimistically replace, and read back one provider."""
        current = self._require(provider_id)
        spec = _normalize_spec(provider_id, payload, current=current)
        profile, routes = _build_profile_and_routes(provider_id, spec)
        entry = self.store.replace(
            profile,
            routes=routes,
            enabled=spec["enabled"],
            expected_revision=expected_revision,
        )
        return ProviderSettingsMutation(self._entry_projection(entry), PROVIDER_EFFECTS)

    def check(
        self,
        provider_id: str,
        *,
        discover_models: bool,
    ) -> dict[str, Any]:
        """Run one explicit bounded check and return content-free evidence."""
        entry = self._require(provider_id)
        service = self.health_services[entry.profile.protocol]
        snapshot = service.check(
            entry,
            discover_models=discover_models,
            force=True,
        )
        return {
            "provider_id": provider_id,
            "health": _health_projection(snapshot),
            "effects": dict(PROVIDER_EFFECTS),
        }

    def _require(self, provider_id: str) -> ProviderRegistryEntry:
        try:
            effective = LayeredProviderRegistry(
                {
                    ProviderOwnership.USER: self.store.list(),
                    ProviderOwnership.MIGRATED_LEGACY: self.migrated_store.list(),
                }
            ).get(provider_id)
        except ValueError as exc:
            raise ProviderSettingsValidationError({"provider_id": str(exc)}) from exc
        if effective is None:
            raise ProviderSettingsNotFoundError(provider_id)
        return effective.entry

    def _effective_entries(self) -> tuple[ProviderRegistryEntry, ...]:
        layered = LayeredProviderRegistry(
            {
                ProviderOwnership.USER: self.store.list(),
                ProviderOwnership.MIGRATED_LEGACY: self.migrated_store.list(),
            }
        )
        return tuple(item.entry for item in layered.list())

    def _entry_projection(self, entry: ProviderRegistryEntry) -> dict[str, Any]:
        profile = entry.profile
        health = self.health_store.load(profile.id)
        if health is not None and health.provider != profile.ref:
            health = None
        compatibility = [
            {
                "harness_id": item.harness_id,
                "adapter_version": item.adapter_version,
                "transports": [transport.value for transport in item.transports],
                "native_auth": item.native_auth,
                "capabilities": list(item.capabilities),
                "evidence_status": "reviewed",
            }
            for item in self.compatibility.list()
            if item.protocol is profile.protocol and profile.dialect in item.dialects
        ]
        return {
            "id": profile.id,
            "display_name": profile.display_name,
            "protocol": profile.protocol.value,
            "dialect": profile.dialect,
            "base_url": profile.base_url,
            "route_prefix": profile.route_prefix,
            "effective_base_url": profile.effective_base_url,
            "source": profile.ownership.value,
            "editable": profile.ownership is ProviderOwnership.USER,
            "enabled": entry.enabled,
            "offline": profile.offline,
            "registry_revision": entry.revision,
            "profile_revision": profile.revision,
            "authentication": _authentication_projection(profile.authentication),
            "default_models": {
                item.purpose.value: item.model for item in profile.default_models
            },
            "routes": [
                {
                    "id": route.id,
                    "revision": route.revision,
                    "purpose": route.purpose.value,
                    "model": route.model,
                    "provider_revision": route.provider.revision,
                    "authentication_ownership": route.authentication_ownership.value,
                }
                for route in entry.routes
            ],
            "compatibility": compatibility,
            "compatibility_explanation": (
                "Only reviewed Harness, dialect, transport, authentication, and capability combinations are admitted."
                if compatibility
                else "No installed Harness adapter has reviewed evidence for this protocol dialect."
            ),
            "health": _health_projection(health) if health is not None else None,
            "effects": dict(PROVIDER_EFFECTS),
            "updated_at": entry.updated_at,
        }


from .settings_codec import (  # noqa: E402, F401
    _authentication_projection,
    _build_profile_and_routes,
    _entry_spec,
    _health_projection,
    _normalize_spec,
)
