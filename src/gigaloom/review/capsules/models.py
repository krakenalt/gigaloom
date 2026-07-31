"""Immutable schema-v1 Run Capsule documents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

RUN_CAPSULE_SCHEMA_VERSION = 1
INPUT_LOCK_KIND = "gigaloom.run_capsule.input_lock.v1"
OUTPUT_RECEIPT_KIND = "gigaloom.run_capsule.output_receipt.v1"
ARTIFACT_MANIFEST_KIND = "gigaloom.run_capsule.artifacts.v1"
OMISSION_MANIFEST_KIND = "gigaloom.run_capsule.omissions.v1"
RUN_CAPSULE_KIND = "gigaloom.run_capsule.v1"


class SignatureStatus(StrEnum):
    """Truthful signature presence state recorded by a capsule."""

    UNSIGNED = "unsigned"
    SIGNED = "signed"


class CostKnowledge(StrEnum):
    """Knowledge state for a retained cost observation."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    """Overall result of offline capsule verification."""

    VERIFIED = "verified"
    DRIFTED = "drifted"


class FindingStatus(StrEnum):
    """Knowledge state for one recomputed input fact."""

    MATCHED = "matched"
    DRIFTED = "drifted"
    UNVERIFIABLE = "unverifiable"
    OMITTED = "omitted"


@dataclass(frozen=True, slots=True)
class _Document:
    _canonical: bytes = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible representation."""
        value = json.loads(self._canonical)
        assert isinstance(value, dict)
        return value


@dataclass(frozen=True, slots=True)
class InputLock(_Document):
    """Content-free facts that define the inputs to one governed run."""

    @property
    def sha256(self) -> str:
        return str(self.to_dict()["input_lock_sha256"])

    @classmethod
    def from_dict(cls, payload: object) -> InputLock:
        from .input_lock import parse_input_lock

        return parse_input_lock(payload)


@dataclass(frozen=True, slots=True)
class OutputReceipt(_Document):
    """Content-free facts observed after one governed run."""

    @property
    def sha256(self) -> str:
        return str(self.to_dict()["output_receipt_sha256"])

    @classmethod
    def from_dict(cls, payload: object) -> OutputReceipt:
        from .output_receipt import parse_output_receipt

        return parse_output_receipt(payload)


@dataclass(frozen=True, slots=True)
class ArtifactManifest(_Document):
    """Bounded metadata for retained artifacts without artifact bytes."""

    @property
    def sha256(self) -> str:
        return str(self.to_dict()["artifacts_sha256"])

    @classmethod
    def from_dict(cls, payload: object) -> ArtifactManifest:
        from .artifacts import parse_artifact_manifest

        return parse_artifact_manifest(payload)


@dataclass(frozen=True, slots=True)
class OmissionManifest(_Document):
    """Explicit, bounded reasons why evidence is absent."""

    @property
    def sha256(self) -> str:
        return str(self.to_dict()["omissions_sha256"])

    @classmethod
    def from_dict(cls, payload: object) -> OmissionManifest:
        from .artifacts import parse_omission_manifest

        return parse_omission_manifest(payload)


@dataclass(frozen=True, slots=True)
class RunCapsule(_Document):
    """Root content-addressed Run Capsule manifest."""

    @property
    def capsule_id(self) -> str:
        return str(self.to_dict()["capsule_id"])

    @property
    def sha256(self) -> str:
        return str(self.to_dict()["capsule_sha256"])

    @classmethod
    def from_dict(cls, payload: object) -> RunCapsule:
        from .artifacts import parse_run_capsule

        return parse_run_capsule(payload)


@dataclass(frozen=True, slots=True)
class RunCapsuleBundle:
    """A complete in-memory capsule ready for deterministic export."""

    capsule: RunCapsule
    input_lock: InputLock
    output_receipt: OutputReceipt
    artifacts: ArtifactManifest
    omissions: OmissionManifest
    signature_manifest: bytes = field(repr=False)
    signature: bytes | None = field(default=None, repr=False)
    public_key_document: bytes | None = field(default=None, repr=False)

    def relative_files(self) -> Mapping[str, bytes]:
        """Return the exact bounded archive members without a root prefix."""
        files = {
            "artifacts.json": self.artifacts._canonical,
            "capsule.json": self.capsule._canonical,
            "input-lock.json": self.input_lock._canonical,
            "omissions.json": self.omissions._canonical,
            "output-receipt.json": self.output_receipt._canonical,
            "signatures/manifest.json": self.signature_manifest,
        }
        if self.signature is not None:
            files["signatures/signature.bin"] = self.signature
        if self.public_key_document is not None:
            files["signatures/public-key.json"] = self.public_key_document
        return dict(sorted(files.items()))


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    """One content-free comparison made during offline verification."""

    field: str
    status: FindingStatus
    expected: str | int | bool | None
    observed: str | int | bool | None


@dataclass(frozen=True, slots=True)
class CapsuleVerificationReport:
    """Bounded offline verification result; never a correctness claim."""

    capsule_id: str
    capsule_sha256: str
    status: VerificationStatus
    signature_status: SignatureStatus
    signature_valid: bool | None
    signer_id: str | None
    trust_status: str | None
    findings: tuple[VerificationFinding, ...]

    @property
    def verified(self) -> bool:
        """Whether integrity passed and no requested comparison drifted."""
        return self.status is VerificationStatus.VERIFIED
