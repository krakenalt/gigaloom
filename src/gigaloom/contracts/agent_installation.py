"""Transactional managed-agent installation and activation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol, cast, runtime_checkable

from gigaloom.contracts.acp_registry import ACPDistributionKind
from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    canonical_digest,
    contains_absolute_path,
    normalize_environment,
    normalize_identities,
    normalize_tokens,
    validate_absolute_path,
    validate_digest,
    validate_https_url,
    validate_identity,
    validate_network_origin,
    validate_optional_digest,
    validate_optional_integrity,
    validate_relative_path,
    validate_schema_version,
    validate_text,
    validate_time_range,
    validate_timestamp,
)


AGENT_INSTALLATION_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class AgentIntegrityPolicy(str, Enum):
    """Artifact integrity admission policy."""

    REQUIRE_VERIFIED = "require_verified"
    ALLOW_EXPLICIT_UNVERIFIED = "allow_explicit_unverified"


class AgentLifecycleScriptPolicy(str, Enum):
    """Package lifecycle-script authority policy."""

    DISABLED = "disabled"
    REVIEWED = "reviewed"


class ManagedAgentStatus(str, Enum):
    """Immutable managed artifact readiness state."""

    STAGED = "staged"
    READY = "ready"
    DEGRADED = "degraded"
    INACTIVE = "inactive"
    FAILED = "failed"


class AgentInstallationOutcome(str, Enum):
    """Terminal installation transaction outcome."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class AgentCleanupStatus(str, Enum):
    """Failure/cancellation staging cleanup evidence."""

    NOT_REQUIRED = "not_required"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"


class AgentActivationStatus(str, Enum):
    """Atomic activation result."""

    READY = "ready"
    DEGRADED = "degraded"
    INACTIVE = "inactive"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class ExtractionLimitsV1:
    """Exact archive/package installation limits."""

    max_bytes: int
    max_files: int
    max_path_depth: int
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="extraction limits")
        for value, lower, upper, label in (
            (self.max_bytes, 1, 2**40, "extraction max bytes"),
            (self.max_files, 1, 1_000_000, "extraction max files"),
            (self.max_path_depth, 1, 256, "extraction max path depth"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not lower <= value <= upper
            ):
                raise ValueError(f"{label} is invalid")


@dataclass(frozen=True, slots=True)
class InstallationTransitionV1:
    """One monotonic content-free installation state transition."""

    sequence: int
    state: str
    timestamp: datetime
    evidence_digest: str
    reason_code: str
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="install transition")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 0 <= self.sequence <= 10_000
        ):
            raise ValueError("installation transition sequence is invalid")
        validate_identity(self.state, field_name="installation transition state")
        validate_timestamp(self.timestamp, field_name="installation transition time")
        validate_digest(
            self.evidence_digest,
            field_name="installation transition evidence digest",
        )
        validate_identity(
            self.reason_code,
            field_name="installation transition reason code",
        )


@dataclass(frozen=True, slots=True)
class AgentInstallPlanV1:
    """Immutable, authority-free plan for exactly one selected distribution."""

    plan_id: str
    registry_id: str
    entry_digest: str
    snapshot_digest: str
    local_agent_id: str
    version: str
    platform: str
    architecture: str
    distribution_kind: ACPDistributionKind
    source: str
    package_or_archive: str
    expected_integrity: str | None
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    network_origins: tuple[str, ...]
    staging_root: str
    managed_root: str
    integrity_policy: AgentIntegrityPolicy
    lifecycle_script_policy: AgentLifecycleScriptPolicy
    side_effects: tuple[str, ...]
    confirmation_required: bool
    expires_at: datetime
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="agent install plan")
        for value, label in (
            (self.plan_id, "agent install plan id"),
            (self.registry_id, "ACP registry id"),
            (self.local_agent_id, "local agent id"),
            (self.platform, "agent target platform"),
            (self.architecture, "agent target architecture"),
        ):
            validate_identity(value, field_name=label)
        validate_digest(self.entry_digest, field_name="ACP entry digest")
        validate_digest(self.snapshot_digest, field_name="ACP snapshot digest")
        validate_text(self.version, field_name="agent version", max_chars=128)
        if not isinstance(self.distribution_kind, ACPDistributionKind):
            raise ValueError("agent distribution kind is invalid")
        validate_https_url(self.source, field_name="agent distribution source")
        validate_text(
            self.package_or_archive,
            field_name="agent package or archive",
            max_chars=1_024,
        )
        validate_optional_integrity(
            self.expected_integrity,
            field_name="agent expected integrity",
        )
        validate_text(self.command, field_name="agent command", max_chars=1_024)
        object.__setattr__(
            self,
            "arguments",
            normalize_tokens(self.arguments, field_name="agent arguments"),
        )
        object.__setattr__(
            self,
            "environment",
            normalize_environment(self.environment),
        )
        object.__setattr__(
            self,
            "network_origins",
            _normalize_origins(self.network_origins),
        )
        staging_root = validate_absolute_path(
            self.staging_root,
            field_name="agent staging root",
        )
        managed_root = validate_absolute_path(
            self.managed_root,
            field_name="agent managed root",
        )
        if staging_root == managed_root:
            raise ValueError("agent staging and managed roots must be distinct")
        if not isinstance(self.integrity_policy, AgentIntegrityPolicy):
            raise ValueError("agent integrity policy is invalid")
        if not isinstance(self.lifecycle_script_policy, AgentLifecycleScriptPolicy):
            raise ValueError("agent lifecycle script policy is invalid")
        side_effects = normalize_identities(
            self.side_effects,
            field_name="agent install side effects",
        )
        object.__setattr__(self, "side_effects", side_effects)
        if not isinstance(self.confirmation_required, bool):
            raise ValueError("agent confirmation flag must be boolean")
        if (
            self.integrity_policy is AgentIntegrityPolicy.REQUIRE_VERIFIED
            and self.expected_integrity is None
        ):
            raise ValueError("verified integrity policy requires expected integrity")
        if (
            self.integrity_policy is AgentIntegrityPolicy.ALLOW_EXPLICIT_UNVERIFIED
            or self.lifecycle_script_policy is AgentLifecycleScriptPolicy.REVIEWED
        ) and not self.confirmation_required:
            raise ValueError("elevated install policy requires explicit confirmation")
        validate_timestamp(self.expires_at, field_name="agent install plan expiry")


@dataclass(frozen=True, slots=True)
class ManagedAgentArtifactV1:
    """Immutable installed artifact projection under a managed root."""

    install_id: str
    registry_id: str
    local_agent_id: str
    version: str
    distribution_kind: ACPDistributionKind
    platform: str
    artifact_digest: str
    package_integrity: str | None
    lock_digest: str
    managed_root: str
    executable_relative_path: str
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    installed_at: datetime
    status: ManagedAgentStatus
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(
            self.schema_version, field_name="managed agent artifact"
        )
        for value, label in (
            (self.install_id, "managed install id"),
            (self.registry_id, "ACP registry id"),
            (self.local_agent_id, "local agent id"),
            (self.platform, "managed agent platform"),
        ):
            validate_identity(value, field_name=label)
        validate_text(self.version, field_name="managed agent version", max_chars=128)
        if not isinstance(self.distribution_kind, ACPDistributionKind):
            raise ValueError("managed distribution kind is invalid")
        validate_digest(self.artifact_digest, field_name="managed artifact digest")
        validate_optional_integrity(
            self.package_integrity,
            field_name="managed package integrity",
        )
        validate_digest(self.lock_digest, field_name="managed lock digest")
        validate_absolute_path(self.managed_root, field_name="managed agent root")
        validate_relative_path(
            self.executable_relative_path,
            field_name="managed executable path",
        )
        validate_text(self.command, field_name="managed agent command", max_chars=1_024)
        object.__setattr__(
            self,
            "arguments",
            normalize_tokens(self.arguments, field_name="managed agent arguments"),
        )
        object.__setattr__(
            self,
            "environment",
            normalize_environment(self.environment),
        )
        validate_timestamp(self.installed_at, field_name="managed agent installed_at")
        if not isinstance(self.status, ManagedAgentStatus):
            raise ValueError("managed agent status is invalid")


@dataclass(frozen=True, slots=True)
class AgentInstallationReceiptV1:
    """Content-free terminal evidence for an installation transaction."""

    receipt_id: str
    plan_id: str
    install_id: str | None
    registry_id: str
    entry_digest: str
    snapshot_digest: str
    transitions: tuple[InstallationTransitionV1, ...]
    bytes_received: int
    artifact_digest: str | None
    package_integrity: str | None
    extraction_limits: ExtractionLimitsV1
    probe_observation_digest: str | None
    activation_id: str | None
    rollback_install_id: str | None
    omissions: tuple[str, ...]
    cleanup_status: AgentCleanupStatus
    outcome: AgentInstallationOutcome
    started_at: datetime
    finished_at: datetime
    content_free: bool = True
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="installation receipt")
        for value, label in (
            (self.receipt_id, "installation receipt id"),
            (self.plan_id, "agent install plan id"),
            (self.registry_id, "ACP registry id"),
        ):
            validate_identity(value, field_name=label)
        for value, label in (
            (self.install_id, "managed install id"),
            (self.activation_id, "agent activation id"),
            (self.rollback_install_id, "rollback install id"),
        ):
            if value is not None:
                validate_identity(value, field_name=label)
        validate_digest(self.entry_digest, field_name="ACP entry digest")
        validate_digest(self.snapshot_digest, field_name="ACP snapshot digest")
        transitions = _normalize_transitions(self.transitions)
        object.__setattr__(self, "transitions", transitions)
        if (
            isinstance(self.bytes_received, bool)
            or not isinstance(self.bytes_received, int)
            or self.bytes_received < 0
        ):
            raise ValueError("installation bytes_received is invalid")
        validate_optional_digest(
            self.artifact_digest,
            field_name="installation artifact digest",
        )
        validate_optional_integrity(
            self.package_integrity,
            field_name="installation package integrity",
        )
        if not isinstance(self.extraction_limits, ExtractionLimitsV1):
            raise ValueError("installation extraction limits are invalid")
        validate_optional_digest(
            self.probe_observation_digest,
            field_name="installation probe observation digest",
        )
        omissions = normalize_identities(
            self.omissions,
            field_name="installation omissions",
        )
        object.__setattr__(self, "omissions", omissions)
        if not isinstance(self.cleanup_status, AgentCleanupStatus):
            raise ValueError("installation cleanup status is invalid")
        if not isinstance(self.outcome, AgentInstallationOutcome):
            raise ValueError("installation outcome is invalid")
        validate_time_range(
            self.started_at,
            self.finished_at,
            field_name="installation receipt",
        )
        if self.outcome is AgentInstallationOutcome.SUCCEEDED:
            if self.install_id is None or self.activation_id is None:
                raise ValueError(
                    "successful installation requires install and activation ids"
                )
            if self.artifact_digest is None and self.package_integrity is None:
                raise ValueError(
                    "successful installation requires exact artifact integrity"
                )
            if self.probe_observation_digest is None:
                raise ValueError(
                    "successful installation requires negotiated probe evidence"
                )
            if self.cleanup_status is not AgentCleanupStatus.NOT_REQUIRED:
                raise ValueError("successful installation cannot claim failure cleanup")
        elif self.cleanup_status is AgentCleanupStatus.NOT_REQUIRED:
            raise ValueError(
                "failed or canceled installation requires cleanup evidence"
            )
        if self.content_free is not True:
            raise ValueError("installation receipts must be content-free")


@dataclass(frozen=True, slots=True)
class AgentActivationV1:
    """Atomic activation bound to artifact and negotiated profile evidence."""

    activation_id: str
    install_id: str
    previous_install_id: str | None
    local_agent_id: str
    profile_digest: str
    compatibility_observation_digest: str
    activated_at: datetime
    status: AgentActivationStatus
    atomic: bool = True
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="agent activation")
        for value, label in (
            (self.activation_id, "agent activation id"),
            (self.install_id, "managed install id"),
            (self.local_agent_id, "local agent id"),
        ):
            validate_identity(value, field_name=label)
        if self.previous_install_id is not None:
            validate_identity(
                self.previous_install_id,
                field_name="previous install id",
            )
            if self.previous_install_id == self.install_id:
                raise ValueError("activation cannot replace an install with itself")
        validate_digest(self.profile_digest, field_name="generated profile digest")
        validate_digest(
            self.compatibility_observation_digest,
            field_name="compatibility observation digest",
        )
        validate_timestamp(self.activated_at, field_name="agent activation time")
        if not isinstance(self.status, AgentActivationStatus):
            raise ValueError("agent activation status is invalid")
        if self.atomic is not True:
            raise ValueError("agent activation must be atomic")


@dataclass(frozen=True, slots=True)
class AgentLockV1:
    """Machine-portable exact inputs for deterministic agent sync."""

    lock_id: str
    registry_id: str
    snapshot_digest: str
    entry_digest: str
    local_agent_id: str
    version: str
    platform: str
    architecture: str
    distribution_kind: ACPDistributionKind
    artifact_digest: str
    package_integrity: str | None
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    generated_profile_digest: str
    content_free: bool = True
    schema_version: int = AGENT_INSTALLATION_SCHEMA_VERSION
    lock_digest: str = field(init=False)

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="agent lock")
        for value, label in (
            (self.lock_id, "agent lock id"),
            (self.registry_id, "ACP registry id"),
            (self.local_agent_id, "local agent id"),
            (self.platform, "agent lock platform"),
            (self.architecture, "agent lock architecture"),
        ):
            validate_identity(value, field_name=label)
        validate_digest(self.snapshot_digest, field_name="ACP snapshot digest")
        validate_digest(self.entry_digest, field_name="ACP entry digest")
        validate_text(self.version, field_name="agent lock version", max_chars=128)
        if not isinstance(self.distribution_kind, ACPDistributionKind):
            raise ValueError("agent lock distribution kind is invalid")
        validate_digest(self.artifact_digest, field_name="agent lock artifact digest")
        validate_optional_integrity(
            self.package_integrity,
            field_name="agent lock package integrity",
        )
        validate_text(self.command, field_name="agent lock command", max_chars=1_024)
        object.__setattr__(
            self,
            "arguments",
            normalize_tokens(self.arguments, field_name="agent lock arguments"),
        )
        object.__setattr__(
            self,
            "environment",
            normalize_environment(self.environment),
        )
        if contains_absolute_path(self.command) or any(
            contains_absolute_path(token)
            for token in (
                *self.arguments,
                *(value for _, value in self.environment),
            )
        ):
            raise ValueError(
                "agent lock cannot contain machine-specific absolute paths"
            )
        validate_digest(
            self.generated_profile_digest,
            field_name="generated profile digest",
        )
        if self.content_free is not True:
            raise ValueError("agent lock must be content-free")
        object.__setattr__(self, "lock_digest", agent_lock_digest(self))


@runtime_checkable
class ManagedAgentInstallerPort(Protocol):
    """Public transactional installer boundary."""

    def install(self, plan: AgentInstallPlanV1) -> AgentInstallationReceiptV1:
        """Execute one already-confirmed immutable plan transactionally."""

    def activate(
        self,
        artifact: ManagedAgentArtifactV1,
        *,
        profile_digest: str,
        compatibility_observation_digest: str,
    ) -> AgentActivationV1:
        """Atomically activate one probed managed artifact."""


def agent_lock_digest(value: AgentLockV1) -> str:
    """Compute the canonical machine-portable lock digest."""
    return canonical_digest(
        {
            "schema_version": value.schema_version,
            "lock_id": value.lock_id,
            "registry_id": value.registry_id,
            "snapshot_digest": value.snapshot_digest,
            "entry_digest": value.entry_digest,
            "local_agent_id": value.local_agent_id,
            "version": value.version,
            "platform": value.platform,
            "architecture": value.architecture,
            "distribution_kind": value.distribution_kind.value,
            "artifact_digest": value.artifact_digest,
            "package_integrity": value.package_integrity,
            "command": value.command,
            "arguments": list(value.arguments),
            "environment": [[key, item] for key, item in value.environment],
            "generated_profile_digest": value.generated_profile_digest,
            "content_free": value.content_free,
        }
    )


def _normalize_origins(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or len(values) > 32:
        raise ValueError("agent network origins must be a non-empty bounded tuple")
    normalized = tuple(
        validate_network_origin(value, field_name="agent network origin")
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError("agent network origins must be unique")
    return tuple(sorted(normalized))


def _normalize_transitions(
    values: object,
) -> tuple[InstallationTransitionV1, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) > 256
        or any(not isinstance(item, InstallationTransitionV1) for item in values)
    ):
        raise ValueError("installation transitions must be a non-empty bounded tuple")
    typed = cast(tuple[InstallationTransitionV1, ...], values)
    expected = tuple(range(len(typed)))
    if tuple(item.sequence for item in typed) != expected:
        raise ValueError(
            "installation transition sequence must be contiguous from zero"
        )
    if any(right.timestamp < left.timestamp for left, right in zip(typed, typed[1:])):
        raise ValueError("installation transition timestamps must be monotonic")
    return typed
