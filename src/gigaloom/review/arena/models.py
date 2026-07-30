"""Immutable contracts for the Reviewed Arena evidence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


REVIEWED_ARENA_SCHEMA_VERSION = 1
CANDIDATE_EVIDENCE_KIND = "gigaloom.reviewed_arena_candidate_evidence.v1"
ARBITRATION_RECEIPT_KIND = "gigaloom.reviewed_arena_arbitration_receipt.v1"


class CandidateStatus(StrEnum):
    """Terminal knowledge state represented by candidate evidence."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class GateOutcome(StrEnum):
    """Closed deterministic gate outcomes."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELED = "canceled"


class ArenaOutcome(StrEnum):
    """Closed Reviewed Arena outcomes."""

    SELECTED = "selected"
    NEEDS_HUMAN = "needs_human"
    NO_ELIGIBLE_CANDIDATE = "no_eligible_candidate"
    REVIEW_FAILED = "review_failed"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """Content-free immutable reference owned by another authority."""

    authority: str
    resource_id: str
    revision: str
    sha256: str


@dataclass(frozen=True, slots=True)
class CandidateIsolation:
    """Opaque identities proving that candidate-owned resources are distinct."""

    worktree_id: str
    native_home_id: str
    terminal_id: str
    provider_session_id: str


@dataclass(frozen=True, slots=True)
class DeterministicGateReceipt:
    """Immutable result of the one required deterministic project gate."""

    gate_id: str
    command_sha256: str
    result_sha256: str
    checked_revision: str
    outcome: GateOutcome
    completed_at: str


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Canonical immutable evidence for one of exactly two candidates."""

    evidence_id: str
    arena_id: str
    candidate_id: str
    ordinal: int
    owner_id: str
    workspace_id: str
    base_revision: str
    run_id: str
    session_id: str
    status: CandidateStatus
    isolation: CandidateIsolation
    run_capsule: EvidenceBinding
    context_manifest: EvidenceBinding
    change_set: EvidenceBinding
    cost_lease_id: str
    cost_receipt: EvidenceBinding
    gate: DeterministicGateReceipt
    created_at: str
    evidence_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the strict content-free wire document."""
        from .codec import candidate_evidence_to_dict

        return candidate_evidence_to_dict(self)

    @classmethod
    def from_dict(cls, payload: object) -> CandidateEvidence:
        """Parse and verify one exact schema-v1 evidence document."""
        from .codec import candidate_evidence_from_dict

        return candidate_evidence_from_dict(payload)


@dataclass(frozen=True, slots=True)
class CandidateEvidenceDigest:
    """Candidate identity bound to its immutable evidence digest."""

    candidate_id: str
    ordinal: int
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ArbitrationReceipt:
    """Canonical outcome over exactly two immutable candidate documents."""

    receipt_id: str
    arena_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    outcome: ArenaOutcome
    candidates: tuple[CandidateEvidenceDigest, CandidateEvidenceDigest]
    selected_candidate_id: str | None
    reviewer_evidence: EvidenceBinding | None
    reason_code: str
    created_at: str
    automatic_apply: bool
    receipt_sha256: str

    def to_dict(self) -> dict[str, object]:
        """Return the strict content-free wire document."""
        from .codec import arbitration_receipt_to_dict

        return arbitration_receipt_to_dict(self)

    @classmethod
    def from_dict(cls, payload: object) -> ArbitrationReceipt:
        """Parse and verify one exact schema-v1 arbitration receipt."""
        from .codec import arbitration_receipt_from_dict

        return arbitration_receipt_from_dict(payload)


__all__ = [
    "ARBITRATION_RECEIPT_KIND",
    "CANDIDATE_EVIDENCE_KIND",
    "REVIEWED_ARENA_SCHEMA_VERSION",
    "ArenaOutcome",
    "ArbitrationReceipt",
    "CandidateEvidence",
    "CandidateEvidenceDigest",
    "CandidateIsolation",
    "CandidateStatus",
    "DeterministicGateReceipt",
    "EvidenceBinding",
    "GateOutcome",
]
