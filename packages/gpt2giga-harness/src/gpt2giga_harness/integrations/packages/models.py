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

from gpt2giga_harness.registries import (
    EntryPointFamily,
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gpt2giga_harness.types import redact_secrets


INTEGRATION_PACKAGE_SCHEMA_VERSION = 1
EXTENSION_TARGET_SCHEMA_VERSION = 1
NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP = "agent_workbench.extension_targets.v1"
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
from .validation import *  # noqa: F403


class IntegrationComponentType(str, Enum):
    """Component families carried by one integration package."""

    MCP = "mcp"
    SKILL = "skill"
    PLUGIN = "plugin"
    EXTENSION = "extension"
    HARNESS_ADAPTER = "harness_adapter"


class IntegrationSourceType(str, Enum):
    """Reviewed source families from which immutable packages may be resolved."""

    CURATED_CATALOG = "curated_catalog"
    PROVIDER_MARKETPLACE = "provider_marketplace"
    GIT = "git"
    LOCAL = "local"
    PACKAGE = "package"
    RAW_MCP = "raw_mcp"


class InstallationScope(str, Enum):
    """Mutation scopes supported by an integration package or target."""

    MANAGED_HOME = "managed_home"
    PROJECT = "project"
    USER_HOME = "user_home"


class IntegrationRequirementType(str, Enum):
    """Security-relevant effect declared by an integration manifest."""

    PERMISSION = "permission"
    SECRET = "secret"
    COMMAND = "command"
    HOOK = "hook"
    BINARY = "binary"
    PACKAGE = "package"
    FILE = "file"
    NETWORK = "network"


class IntegrationPolicyClass(str, Enum):
    """Fail-closed policy classification for a declared package effect."""

    REVIEW_REQUIRED = "review_required"
    EXPLICIT_APPROVAL = "explicit_approval"
    PROVIDER_HANDOFF = "provider_handoff"
    FORBIDDEN = "forbidden"


class IntegrationTrustKind(str, Enum):
    """Supply-chain evidence classes retained without raw reports."""

    SOURCE = "source"
    PUBLISHER = "publisher"
    LICENSE = "license"
    SIGNATURE = "signature"
    SCAN = "scan"


class IntegrationTrustStatus(str, Enum):
    """Truthful status of one trust claim."""

    VERIFIED = "verified"
    DELEGATED = "delegated"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"


class IntegrationUpdatePolicy(str, Enum):
    """How a newer immutable package may be selected."""

    PINNED = "pinned"
    MANUAL_REVIEW = "manual_review"
    TRACK_CHANNEL_WITH_REVIEW = "track_channel_with_review"


class IntegrationTrustDecision(str, Enum):
    """Highest policy gate required before any installation work."""

    REVIEW_REQUIRED = "review_required"
    EXPLICIT_APPROVAL = "explicit_approval"
    PROVIDER_HANDOFF = "provider_handoff"
    BLOCKED = "blocked"


@dataclass(frozen=True, order=True)
class IntegrationComponent:
    """One portable or target-specific component in a package."""

    id: str
    type: IntegrationComponentType
    portable: bool

    def __post_init__(self) -> None:
        _validate_identity(self.id, field_name="component id")
        if not isinstance(self.type, IntegrationComponentType):
            raise ValueError("component type is invalid")
        if not isinstance(self.portable, bool):
            raise ValueError("component portable must be a boolean")


@dataclass(frozen=True, order=True)
class IntegrationRequirement:
    """Content-free declaration of one privileged package requirement."""

    id: str
    type: IntegrationRequirementType
    classification: IntegrationPolicyClass
    reason: str
    argv: tuple[str, ...] = ()
    locator: str | None = None
    checksum: str | None = None
    secret_owner: str | None = None
    environment: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.id, field_name="requirement id")
        if not isinstance(self.type, IntegrationRequirementType):
            raise ValueError("requirement type is invalid")
        if not isinstance(self.classification, IntegrationPolicyClass):
            raise ValueError("requirement classification is invalid")
        _validate_text(self.reason, field_name="requirement reason")
        object.__setattr__(self, "argv", _normalize_argv(self.argv))
        object.__setattr__(
            self,
            "environment",
            _normalize_environment_names(self.environment),
        )
        if self.locator is not None:
            _validate_text(self.locator, field_name="requirement locator")
        if self.checksum is not None:
            _validate_checksum(self.checksum, field_name="requirement checksum")
        if self.secret_owner is not None:
            _validate_identity(self.secret_owner, field_name="secret owner")
        self._validate_shape()

    def _validate_shape(self) -> None:
        command_types = {
            IntegrationRequirementType.COMMAND,
            IntegrationRequirementType.HOOK,
        }
        artifact_types = {
            IntegrationRequirementType.BINARY,
            IntegrationRequirementType.PACKAGE,
            IntegrationRequirementType.FILE,
        }
        if self.type in command_types:
            if not self.argv or self.locator is not None or self.checksum is not None:
                raise ValueError("command and hook requirements use explicit argv only")
            if self.secret_owner is not None:
                raise ValueError("commands and hooks cannot own secrets")
            return
        if self.type in artifact_types:
            if self.locator is None or self.checksum is None:
                raise ValueError("artifact requirements require locator and checksum")
            if self.argv or self.secret_owner is not None or self.environment:
                raise ValueError("artifact requirement fields are invalid")
            return
        if self.type is IntegrationRequirementType.NETWORK:
            if self.locator is None:
                raise ValueError("network requirements require an HTTPS origin")
            object.__setattr__(self, "locator", _canonical_https_origin(self.locator))
            if self.argv or self.checksum is not None or self.secret_owner is not None:
                raise ValueError("network requirement fields are invalid")
            return
        if self.type is IntegrationRequirementType.SECRET:
            if self.secret_owner is None:
                raise ValueError("secret requirements require a backend owner")
            if self.argv or self.locator is not None or self.checksum is not None:
                raise ValueError("secret requirements cannot retain values or commands")
            if self.environment:
                raise ValueError("secret requirements declare ownership, not values")
            return
        if self.type is IntegrationRequirementType.PERMISSION:
            if (
                self.argv
                or self.locator is not None
                or self.checksum is not None
                or self.secret_owner is not None
                or self.environment
            ):
                raise ValueError("permission requirement fields are invalid")


@dataclass(frozen=True, order=True)
class IntegrationCompatibility:
    """Version and capability constraint for one extension target."""

    target_id: str
    minimum_version: str | None = None
    maximum_version_exclusive: str | None = None
    required_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.target_id, field_name="compatibility target id")
        for field_name in ("minimum_version", "maximum_version_exclusive"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_identity(value, field_name=f"compatibility {field_name}")
        object.__setattr__(
            self,
            "required_capabilities",
            _normalize_identities(
                self.required_capabilities,
                field_name="required capability",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, order=True)
class IntegrationTargetOverlay:
    """Target-specific projection that never erases the portable core."""

    target_id: str
    component_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.target_id, field_name="overlay target id")
        object.__setattr__(
            self,
            "component_ids",
            _normalize_identities(
                self.component_ids,
                field_name="overlay component id",
            ),
        )
        object.__setattr__(
            self,
            "requirement_ids",
            _normalize_identities(
                self.requirement_ids,
                field_name="overlay requirement id",
                allow_empty=True,
            ),
        )


@dataclass(frozen=True, order=True)
class IntegrationTrustEvidence:
    """Bounded trust result without raw scanner, signature, or publisher data."""

    id: str
    kind: IntegrationTrustKind
    status: IntegrationTrustStatus
    authority: str
    revision: str

    def __post_init__(self) -> None:
        _validate_identity(self.id, field_name="trust evidence id")
        if not isinstance(self.kind, IntegrationTrustKind):
            raise ValueError("trust evidence kind is invalid")
        if not isinstance(self.status, IntegrationTrustStatus):
            raise ValueError("trust evidence status is invalid")
        _validate_identity(self.authority, field_name="trust evidence authority")
        _validate_identity(self.revision, field_name="trust evidence revision")


@dataclass(frozen=True)
class IntegrationPackage:
    """Strict immutable manifest for a provider-neutral integration package."""

    id: str
    version: str
    publisher: str
    license: str
    source_type: IntegrationSourceType
    source: str
    immutable_ref: str
    checksum: str
    components: tuple[IntegrationComponent, ...]
    requirements: tuple[IntegrationRequirement, ...]
    overlays: tuple[IntegrationTargetOverlay, ...]
    compatibility: tuple[IntegrationCompatibility, ...]
    scopes: tuple[InstallationScope, ...]
    update_policy: IntegrationUpdatePolicy
    verification_steps: tuple[str, ...]
    rollback_steps: tuple[str, ...]
    trust_evidence: tuple[IntegrationTrustEvidence, ...] = ()
    schema_version: int = INTEGRATION_PACKAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != INTEGRATION_PACKAGE_SCHEMA_VERSION:
            raise ValueError("unsupported integration package schema_version")
        _validate_identity(self.id, field_name="integration id")
        _validate_identity(self.version, field_name="integration version")
        _validate_identity(self.publisher, field_name="integration publisher")
        _validate_identity(self.license, field_name="integration license")
        if not isinstance(self.source_type, IntegrationSourceType):
            raise ValueError("integration source_type is invalid")
        _validate_text(self.source, field_name="integration source")
        _validate_identity(self.immutable_ref, field_name="integration immutable_ref")
        _validate_checksum(self.checksum, field_name="integration checksum")
        object.__setattr__(
            self,
            "components",
            _normalize_records(
                self.components,
                expected_type=IntegrationComponent,
                field_name="integration component",
            ),
        )
        object.__setattr__(
            self,
            "requirements",
            _normalize_records(
                self.requirements,
                expected_type=IntegrationRequirement,
                field_name="integration requirement",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "overlays",
            _normalize_records(
                self.overlays,
                expected_type=IntegrationTargetOverlay,
                field_name="integration overlay",
                id_attribute="target_id",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "compatibility",
            _normalize_records(
                self.compatibility,
                expected_type=IntegrationCompatibility,
                field_name="integration compatibility",
                id_attribute="target_id",
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "scopes", _normalize_scopes(self.scopes))
        if not isinstance(self.update_policy, IntegrationUpdatePolicy):
            raise ValueError("integration update_policy is invalid")
        object.__setattr__(
            self,
            "verification_steps",
            _normalize_identities(
                self.verification_steps,
                field_name="verification step",
            ),
        )
        object.__setattr__(
            self,
            "rollback_steps",
            _normalize_identities(self.rollback_steps, field_name="rollback step"),
        )
        object.__setattr__(
            self,
            "trust_evidence",
            _normalize_records(
                self.trust_evidence,
                expected_type=IntegrationTrustEvidence,
                field_name="trust evidence",
                allow_empty=True,
            ),
        )
        self._validate_references()

    def _validate_references(self) -> None:
        component_ids = {item.id for item in self.components}
        requirement_ids = {item.id for item in self.requirements}
        target_ids = {item.target_id for item in self.compatibility}
        for overlay in self.overlays:
            if overlay.target_id not in target_ids:
                raise ValueError("overlay target requires a compatibility contract")
            if not set(overlay.component_ids) <= component_ids:
                raise ValueError("overlay references an unknown component")
            if not set(overlay.requirement_ids) <= requirement_ids:
                raise ValueError("overlay references an unknown requirement")
        target_specific = {item.id for item in self.components if not item.portable}
        projected = {
            component_id
            for overlay in self.overlays
            for component_id in overlay.component_ids
        }
        if not target_specific <= projected:
            raise ValueError("target-specific components require a target overlay")


@dataclass(frozen=True, order=True)
class IntegrationTrustDiagnostic:
    """Content-free policy result bound only to a stable manifest subject."""

    code: str
    subject_id: str
    classification: IntegrationTrustDecision


@dataclass(frozen=True)
class IntegrationTrustAssessment:
    """Bounded trust preview which never authorizes installation."""

    package_id: str
    package_version: str
    manifest_hash: str
    decision: IntegrationTrustDecision
    install_authorized: bool
    diagnostics: tuple[IntegrationTrustDiagnostic, ...]


@dataclass(frozen=True)
class ExtensionTargetDescriptor:
    """Versioned capability declaration for one extension installation target."""

    id: str
    revision: str
    component_types: tuple[IntegrationComponentType, ...]
    scopes: tuple[InstallationScope, ...]
    capabilities: tuple[str, ...]
    trust_evidence: tuple[IntegrationTrustEvidence, ...]
    schema_version: int = EXTENSION_TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != EXTENSION_TARGET_SCHEMA_VERSION:
            raise ValueError("unsupported extension target schema_version")
        _validate_identity(self.id, field_name="extension target id")
        _validate_identity(self.revision, field_name="extension target revision")
        types = tuple(sorted(set(self.component_types), key=lambda item: item.value))
        if not types or any(
            not isinstance(item, IntegrationComponentType) for item in types
        ):
            raise ValueError("extension target component_types are invalid")
        object.__setattr__(self, "component_types", types)
        object.__setattr__(self, "scopes", _normalize_scopes(self.scopes))
        object.__setattr__(
            self,
            "capabilities",
            _normalize_identities(self.capabilities, field_name="target capability"),
        )
        object.__setattr__(
            self,
            "trust_evidence",
            _normalize_records(
                self.trust_evidence,
                expected_type=IntegrationTrustEvidence,
                field_name="target trust evidence",
            ),
        )


@runtime_checkable
class ExtensionTargetDriver(Protocol):
    """Provider-neutral lifecycle surface implemented by target adapters."""

    descriptor: ExtensionTargetDescriptor

    def probe_target(self) -> object: ...

    def discover_installed(self) -> object: ...

    def preview_install(self) -> object: ...

    def install(self) -> object: ...

    def verify(self) -> object: ...

    def enable(self) -> object: ...

    def disable(self) -> object: ...

    def preview_update(self) -> object: ...

    def update(self) -> object: ...

    def preview_uninstall(self) -> object: ...

    def uninstall(self) -> object: ...

    def rollback(self) -> object: ...


@dataclass(frozen=True)
class ExtensionTargetPlugin:
    """Discoverable descriptor and lazy driver factory."""

    descriptor: ExtensionTargetDescriptor
    factory: Callable[[], ExtensionTargetDriver]

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, ExtensionTargetDescriptor):
            raise ValueError("extension target descriptor is invalid")
        if not callable(self.factory):
            raise ValueError("extension target factory must be callable")


__all__ = [name for name in globals() if not name.startswith("__")]
