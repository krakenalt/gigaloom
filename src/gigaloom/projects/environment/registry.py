"""Environment provider registry and compatibility facade."""

from __future__ import annotations

import hashlib
from importlib.metadata import entry_points
import json
from typing import Any

from gigaloom.registries import (
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gigaloom.types import redact_secrets

from .models import (
    ENVIRONMENT_PROVIDER_ENTRY_POINTS,
    ENVIRONMENT_SNAPSHOT_SCHEMA_VERSION,
    GIT_TIMEOUT_SECONDS,
    MAX_CAPTURED_PATHS,
    MAX_CHANGED_PATHS,
    MAX_COMMAND_OUTPUT_BYTES,
    MAX_DIFF_HASH_BYTES,
    EnvironmentProvider,
    EnvironmentProviderDescriptor,
    EnvironmentProviderPlugin,
    EnvironmentCaptureError,
    EnvironmentSnapshot,
    HostedRepositoryHint,
    MAX_DISCOVERY_ERRORS,
    MAX_DISCOVERY_ERROR_CHARS,
    MAX_PATH_CHARS,
    NEUTRAL_ENVIRONMENT_ENTRY_POINT_GROUP,
)

from .capture import (
    GIT_ENVIRONMENT_DESCRIPTOR,
    GitEnvironmentProvider,
)

__all__ = [
    "ENVIRONMENT_PROVIDER_ENTRY_POINTS",
    "ENVIRONMENT_SNAPSHOT_SCHEMA_VERSION",
    "GIT_ENVIRONMENT_DESCRIPTOR",
    "GIT_TIMEOUT_SECONDS",
    "MAX_CAPTURED_PATHS",
    "MAX_CHANGED_PATHS",
    "MAX_COMMAND_OUTPUT_BYTES",
    "MAX_DIFF_HASH_BYTES",
    "MAX_DISCOVERY_ERRORS",
    "MAX_DISCOVERY_ERROR_CHARS",
    "MAX_PATH_CHARS",
    "NEUTRAL_ENVIRONMENT_ENTRY_POINT_GROUP",
    "EnvironmentCaptureError",
    "EnvironmentProvider",
    "EnvironmentProviderDescriptor",
    "EnvironmentProviderPlugin",
    "EnvironmentProviderRegistry",
    "EnvironmentSnapshot",
    "GitEnvironmentProvider",
    "HostedRepositoryHint",
    "git_environment_provider_plugin",
]


class EnvironmentProviderRegistry:
    """Discover environment providers through the neutral v1 registry kernel."""

    def __init__(self) -> None:
        self._kernel = VersionedRegistryKernel[EnvironmentProviderPlugin](
            ENVIRONMENT_PROVIDER_ENTRY_POINTS
        )
        self.discovery_errors: list[str] = []

    @classmethod
    def with_builtins(cls) -> EnvironmentProviderRegistry:
        """Create a registry containing the local Git provider."""
        registry = cls()
        registry._register(
            git_environment_provider_plugin(),
            identity=_implementation_identity(git_environment_provider_plugin),
            source="built-in:git_environment_provider_plugin",
        )
        return registry

    def register(self, plugin: EnvironmentProviderPlugin) -> RegistrationOutcome:
        """Register one runtime provider."""
        return self._register(
            plugin,
            identity=_plugin_identity(plugin),
            source=f"runtime:{plugin.descriptor.id}",
        )

    def _register(
        self,
        plugin: EnvironmentProviderPlugin,
        *,
        identity: str,
        source: str,
        allow_equivalent_duplicate: bool = False,
    ) -> RegistrationOutcome:
        if not isinstance(plugin, EnvironmentProviderPlugin):
            raise TypeError("environment entry point must return a provider plugin")
        return self._kernel.register(
            item_id=plugin.descriptor.id,
            item=plugin,
            identity=identity,
            source=source,
            allow_equivalent_duplicate=allow_equivalent_duplicate,
        )

    def list(self) -> tuple[EnvironmentProviderDescriptor, ...]:
        """Return provider declarations in deterministic order."""
        return tuple(
            plugin.descriptor
            for plugin in sorted(
                self._kernel.values(), key=lambda item: item.descriptor.id
            )
        )

    def create_provider(self, provider_id: str) -> EnvironmentProvider:
        """Create one provider and verify its declared identity."""
        plugin = self._kernel.get(provider_id)
        if plugin is None:
            raise KeyError(provider_id)
        provider = plugin.factory()
        descriptor = getattr(provider, "descriptor", None)
        snapshot = getattr(provider, "snapshot", None)
        if descriptor != plugin.descriptor or not callable(snapshot):
            raise TypeError("environment provider factory returned an invalid provider")
        return provider

    def load_entry_points(self) -> None:
        """Load third-party providers with bounded redaction-safe failures."""
        try:
            all_entry_points = entry_points()
        except Exception as exc:  # pragma: no cover - defensive importlib path
            self._record_discovery_error(
                "Environment provider discovery failed: "
                f"{type(exc).__name__} (details omitted)."
            )
            return
        selected = sorted(
            _select_entry_points(
                all_entry_points, ENVIRONMENT_PROVIDER_ENTRY_POINTS.primary_group
            ),
            key=_entry_point_sort_key,
        )
        for entry_point in selected:
            entry_name = str(getattr(entry_point, "name", "<unnamed>"))
            source = (
                f"entry-point:{ENVIRONMENT_PROVIDER_ENTRY_POINTS.primary_group}:"
                f"{entry_name}"
            )
            try:
                loaded = entry_point.load()
                plugin = _load_entry_point_plugin(loaded)
                self._register(
                    plugin,
                    identity=_entry_point_identity(entry_point, loaded, plugin),
                    source=source,
                    allow_equivalent_duplicate=True,
                )
            except RegistryCollisionError as exc:
                self._record_discovery_error(
                    "Environment provider id collision for "
                    f"{exc.item_id!r}: keeping {exc.existing_source}; "
                    f"rejected {exc.incoming_source}."
                )
            except Exception as exc:  # pragma: no cover - plugin failure path
                self._record_discovery_error(
                    f"{source}: {type(exc).__name__} (details omitted)."
                )

    def _record_discovery_error(self, message: str) -> None:
        if len(self.discovery_errors) >= MAX_DISCOVERY_ERRORS:
            return
        safe = str(redact_secrets(message))
        self.discovery_errors.append(safe[:MAX_DISCOVERY_ERROR_CHARS])


def git_environment_provider_plugin() -> EnvironmentProviderPlugin:
    """Return the built-in provider plugin exposed through package metadata."""
    return EnvironmentProviderPlugin(
        descriptor=GIT_ENVIRONMENT_DESCRIPTOR,
        factory=GitEnvironmentProvider,
    )


# Keep the packaged entry-point identity stable across the compatibility move.
git_environment_provider_plugin.__module__ = "gigaloom.environments"


def _plugin_identity(plugin: EnvironmentProviderPlugin) -> str:
    payload = {
        "descriptor": {
            "id": plugin.descriptor.id,
            "display_name": plugin.descriptor.display_name,
            "capabilities": plugin.descriptor.capabilities,
            "schema_version": plugin.descriptor.schema_version,
        },
        "factory": _implementation_identity(plugin.factory),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _select_entry_points(all_entry_points: Any, group: str):
    if hasattr(all_entry_points, "select"):
        return all_entry_points.select(group=group)
    return all_entry_points.get(group, ())


def _entry_point_sort_key(entry_point: Any) -> tuple[str, str]:
    return (
        str(getattr(entry_point, "name", "")),
        str(getattr(entry_point, "value", "")),
    )


def _entry_point_identity(
    entry_point: Any, loaded: Any, plugin: EnvironmentProviderPlugin
) -> str:
    value = getattr(entry_point, "value", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"{_implementation_identity(loaded)}:{_plugin_identity(plugin)}"


def _implementation_identity(implementation: Any) -> str:
    module = getattr(implementation, "__module__", type(implementation).__module__)
    qualname = getattr(
        implementation, "__qualname__", type(implementation).__qualname__
    )
    return f"{module}:{qualname}"


def _load_entry_point_plugin(loaded: Any) -> EnvironmentProviderPlugin:
    value = loaded() if callable(loaded) else loaded
    if not isinstance(value, EnvironmentProviderPlugin):
        raise TypeError("environment entry point did not create a provider plugin")
    return value
