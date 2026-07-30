# ruff: noqa: E402, F401, F403, F405
"""Provider-neutral integration package and target discovery contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
from importlib.metadata import entry_points
import json
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from gigaloom.registries import (
    EntryPointFamily,
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gigaloom.types import redact_secrets


INTEGRATION_PACKAGE_SCHEMA_VERSION = 1
EXTENSION_TARGET_SCHEMA_VERSION = 1
NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP = "gigaloom.extension_targets.v1"
EXTENSION_TARGET_ENTRY_POINTS = EntryPointFamily(
    registry_id="extension_target",
    api_version=1,
    primary_group=NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP,
)
MAX_TARGET_DISCOVERY_ERRORS = 20
MAX_TARGET_DISCOVERY_ERROR_CHARS = 400
MAX_TRUST_DIAGNOSTICS = 100
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CHECKSUM_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
from .codec import *  # noqa: F403
from .models import *  # noqa: F403
from .trust import *  # noqa: F403
from .validation import *  # noqa: F403


class ExtensionTargetRegistry:
    """Discover extension targets through the neutral v1 registry family."""

    def __init__(self) -> None:
        self._kernel = VersionedRegistryKernel[ExtensionTargetPlugin](
            EXTENSION_TARGET_ENTRY_POINTS
        )
        self.discovery_errors: list[str] = []

    def register(self, plugin: ExtensionTargetPlugin) -> RegistrationOutcome:
        """Register one runtime extension target."""
        return self._register(
            plugin,
            identity=_plugin_identity(plugin),
            source=f"runtime:{plugin.descriptor.id}",
        )

    def _register(
        self,
        plugin: ExtensionTargetPlugin,
        *,
        identity: str,
        source: str,
        allow_equivalent_duplicate: bool = False,
    ) -> RegistrationOutcome:
        if not isinstance(plugin, ExtensionTargetPlugin):
            raise TypeError("extension target entry point must return a plugin")
        return self._kernel.register(
            item_id=plugin.descriptor.id,
            item=plugin,
            identity=identity,
            source=source,
            allow_equivalent_duplicate=allow_equivalent_duplicate,
        )

    def list(self) -> tuple[ExtensionTargetDescriptor, ...]:
        """Return target declarations in deterministic order."""
        return tuple(
            item.descriptor
            for item in sorted(
                self._kernel.values(),
                key=lambda plugin: plugin.descriptor.id,
            )
        )

    def create_driver(self, target_id: str) -> ExtensionTargetDriver:
        """Create one driver and reject a mismatched or incomplete factory result."""
        plugin = self._kernel.get(target_id)
        if plugin is None:
            raise KeyError(target_id)
        driver = plugin.factory()
        if not isinstance(driver, ExtensionTargetDriver):
            raise TypeError("extension target factory returned an invalid driver")
        if driver.descriptor != plugin.descriptor:
            raise ValueError(
                "extension target driver descriptor does not match registry"
            )
        return driver

    def load_entry_points(self) -> None:
        """Load third-party target plugins with bounded redaction-safe failures."""
        try:
            all_entry_points = entry_points()
        except Exception as exc:  # pragma: no cover - defensive importlib path
            self._record_discovery_error(
                "Extension target discovery failed: "
                f"{type(exc).__name__} (details omitted)."
            )
            return
        selected = sorted(
            _select_entry_points(
                all_entry_points,
                EXTENSION_TARGET_ENTRY_POINTS.primary_group,
            ),
            key=_entry_point_sort_key,
        )
        for entry_point in selected:
            entry_name = str(getattr(entry_point, "name", "<unnamed>"))
            source = (
                f"entry-point:{EXTENSION_TARGET_ENTRY_POINTS.primary_group}:"
                f"{entry_name}"
            )
            try:
                loaded = entry_point.load()
                plugin = _load_target_plugin(loaded)
                self._register(
                    plugin,
                    identity=_entry_point_identity(entry_point, loaded, plugin),
                    source=source,
                    allow_equivalent_duplicate=True,
                )
            except RegistryCollisionError as exc:
                self._record_discovery_error(
                    "Extension target id collision for "
                    f"{exc.item_id!r}: keeping {exc.existing_source}; "
                    f"rejected {exc.incoming_source}."
                )
            except Exception as exc:  # pragma: no cover - plugin failure path
                self._record_discovery_error(
                    f"{source}: {type(exc).__name__} (details omitted)."
                )

    def _record_discovery_error(self, message: str) -> None:
        if len(self.discovery_errors) >= MAX_TARGET_DISCOVERY_ERRORS:
            return
        safe_message = str(redact_secrets(message))
        self.discovery_errors.append(safe_message[:MAX_TARGET_DISCOVERY_ERROR_CHARS])


def _plugin_identity(plugin: ExtensionTargetPlugin) -> str:
    payload = extension_target_descriptor_to_dict(plugin.descriptor)
    payload["factory"] = _implementation_identity(plugin.factory)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _load_target_plugin(loaded: Any) -> ExtensionTargetPlugin:
    value = loaded() if callable(loaded) else loaded
    if not isinstance(value, ExtensionTargetPlugin):
        raise TypeError("extension target entry point did not create a plugin")
    return value


def _entry_point_identity(
    entry_point: Any,
    loaded: Any,
    plugin: ExtensionTargetPlugin,
) -> str:
    value = getattr(entry_point, "value", None)
    if isinstance(value, str) and value.strip():
        return hashlib.sha256(
            f"{value.strip()}:{_plugin_identity(plugin)}".encode("utf-8")
        ).hexdigest()
    return hashlib.sha256(
        f"{_implementation_identity(loaded)}:{_plugin_identity(plugin)}".encode("utf-8")
    ).hexdigest()


def _implementation_identity(value: Any) -> str:
    module = getattr(value, "__module__", type(value).__module__)
    qualname = getattr(value, "__qualname__", type(value).__qualname__)
    return f"{module}:{qualname}"


def _select_entry_points(all_entry_points: Any, group: str):
    if hasattr(all_entry_points, "select"):
        return all_entry_points.select(group=group)
    return all_entry_points.get(group, ())


def _entry_point_sort_key(entry_point: Any) -> tuple[str, str]:
    return (
        str(getattr(entry_point, "name", "")),
        str(getattr(entry_point, "value", "")),
    )


__all__ = [
    "EXTENSION_TARGET_ENTRY_POINTS",
    "EXTENSION_TARGET_SCHEMA_VERSION",
    "ExtensionTargetDescriptor",
    "ExtensionTargetDriver",
    "ExtensionTargetPlugin",
    "ExtensionTargetRegistry",
    "INTEGRATION_PACKAGE_SCHEMA_VERSION",
    "InstallationScope",
    "IntegrationCompatibility",
    "IntegrationComponent",
    "IntegrationComponentType",
    "IntegrationPackage",
    "IntegrationPolicyClass",
    "IntegrationRequirement",
    "IntegrationRequirementType",
    "IntegrationSourceType",
    "IntegrationTargetOverlay",
    "IntegrationTrustAssessment",
    "IntegrationTrustDecision",
    "IntegrationTrustDiagnostic",
    "IntegrationTrustEvidence",
    "IntegrationTrustKind",
    "IntegrationTrustStatus",
    "IntegrationUpdatePolicy",
    "NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP",
    "assess_integration_package",
    "extension_target_descriptor_from_dict",
    "extension_target_descriptor_to_dict",
    "integration_package_from_dict",
    "integration_package_semantic_hash",
    "integration_package_to_dict",
    "integration_trust_assessment_to_dict",
]
