"""Strict Web projections for content-free Run Capsule evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CapsuleSignatureEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["unsigned", "signed"]
    valid: bool | None
    signer_id: str | None
    trust_status: str | None


class CapsuleDriftFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    status: Literal["matched", "drifted", "unverifiable", "omitted"]
    expected: str | int | bool | None
    observed: str | int | bool | None


class CapsuleDriftEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["current", "drifted", "unverifiable"]
    matched_count: int = Field(ge=0)
    drifted_count: int = Field(ge=0)
    unverifiable_count: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    findings: tuple[CapsuleDriftFinding, ...] = Field(max_length=64)


class CapsuleRunReference(BaseModel):
    """One source or destination Run Capsule digest bound by a lane delta."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["source", "destination"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["captured", "not_captured"]


class CapsuleLaneDeltaReference(BaseModel):
    """Verified content-free lane packet referenced beside a Run Capsule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["gigaloom.lane_delta.reference.v1"]
    packet_id: str
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    source_lane_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    destination_lane_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changed_selectors: tuple[str, ...] = Field(max_length=5)
    changed_anchor_reason_codes: tuple[str, ...] = Field(max_length=9)
    run_capsule_references: tuple[CapsuleRunReference, ...] = Field(
        min_length=2,
        max_length=2,
    )
    disclosure_mode: Literal["packet"]
    content_mode: Literal["content_free"]
    content_free: Literal[True]
    hidden_state_portability_claimed: Literal[False]
    omissions: tuple[str, ...] = Field(max_length=128)


class RunCapsuleWebEvidence(BaseModel):
    """Bounded evidence surface; integrity never implies correctness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    kind: Literal["gigaloom.run_capsule.web_evidence.v1"] = (
        "gigaloom.run_capsule.web_evidence.v1"
    )
    run_id: str
    capsule_id: str
    capsule_sha256: str
    archive_sha256: str
    created_at: str
    content_free: Literal[True] = True
    integrity_status: Literal["verified"] = "verified"
    correctness_claimed: Literal[False] = False
    signature: CapsuleSignatureEvidence
    drift: CapsuleDriftEvidence
    references: tuple[CapsuleLaneDeltaReference, ...] = Field(
        default=(),
        max_length=16,
    )
    export_path: str


__all__ = [
    "CapsuleDriftEvidence",
    "CapsuleDriftFinding",
    "CapsuleLaneDeltaReference",
    "CapsuleRunReference",
    "CapsuleSignatureEvidence",
    "RunCapsuleWebEvidence",
]
