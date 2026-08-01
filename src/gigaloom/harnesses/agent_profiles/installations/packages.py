"""Immutable exact package resolution and install result evidence."""

from __future__ import annotations

from dataclasses import dataclass

from gigaloom.contracts import ManagedAgentArtifactV1
from gigaloom.contracts.operational_validation import (
    validate_digest,
    validate_integrity,
    validate_text,
)
from gigaloom.harnesses.agent_profiles.installations.models import (
    DistributionResolutionV1,
)


MAX_PACKAGE_LOCK_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NpxPackageResolution:
    """Canonical npm lock and registry integrity bound to one distribution."""

    evidence: DistributionResolutionV1
    package: str
    package_name: str
    version: str
    lock_bytes: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, DistributionResolutionV1):
            raise ValueError("npm resolution evidence is invalid")
        for value, label in (
            (self.package, "npm exact package"),
            (self.package_name, "npm package name"),
            (self.version, "npm package version"),
        ):
            validate_text(value, field_name=label, max_chars=1_024)
        _validate_lock(self.lock_bytes, label="npm lock")
        if self.evidence.package_integrity is None or self.evidence.lock_digest is None:
            raise ValueError("npm resolution requires integrity and lock digest")
        if self.evidence.lock_digest != _digest(self.lock_bytes):
            raise ValueError("npm lock digest does not match lock bytes")
        if self.evidence.artifact_digest != self.evidence.lock_digest:
            raise ValueError("npm artifact identity must be its exact lock digest")


@dataclass(frozen=True, slots=True)
class UvxPackageResolution:
    """Canonical uv lock and interpreter binding for one distribution."""

    evidence: DistributionResolutionV1
    package: str
    package_name: str
    version: str
    lock_bytes: bytes
    interpreter: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, DistributionResolutionV1):
            raise ValueError("uvx resolution evidence is invalid")
        for value, label in (
            (self.package, "uvx exact package"),
            (self.package_name, "uvx package name"),
            (self.version, "uvx package version"),
            (self.interpreter, "uvx interpreter"),
        ):
            validate_text(value, field_name=label, max_chars=1_024)
        _validate_lock(self.lock_bytes, label="uv lock")
        if (
            self.evidence.lock_digest is None
            or self.evidence.interpreter_fingerprint is None
        ):
            raise ValueError("uvx resolution requires lock and interpreter evidence")
        if self.evidence.lock_digest != _digest(self.lock_bytes):
            raise ValueError("uv lock digest does not match lock bytes")
        if self.evidence.artifact_digest != self.evidence.lock_digest:
            raise ValueError("uvx artifact identity must be its exact lock digest")


@dataclass(frozen=True, slots=True)
class PackageInstallResult:
    """Installed-but-inactive private package environment."""

    artifact: ManagedAgentArtifactV1
    lock_digest: str
    package_integrity: str | None
    command_evidence_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ManagedAgentArtifactV1):
            raise ValueError("package install artifact is invalid")
        validate_digest(self.lock_digest, field_name="package install lock digest")
        if self.package_integrity is not None:
            validate_integrity(
                self.package_integrity,
                field_name="package install integrity",
            )
        validate_digest(
            self.command_evidence_digest,
            field_name="package command evidence digest",
        )


def _validate_lock(value: object, *, label: str) -> bytes:
    if not isinstance(value, bytes) or not value or len(value) > MAX_PACKAGE_LOCK_BYTES:
        raise ValueError(f"{label} bytes are invalid")
    return value


def _digest(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()
