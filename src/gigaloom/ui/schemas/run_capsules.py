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
    export_path: str


__all__ = [
    "CapsuleDriftEvidence",
    "CapsuleDriftFinding",
    "CapsuleSignatureEvidence",
    "RunCapsuleWebEvidence",
]
