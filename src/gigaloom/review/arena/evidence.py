"""Builders for immutable Reviewed Arena evidence and receipts."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .codec import (
    arbitration_receipt_to_dict,
    candidate_evidence_to_dict,
    canonical_sha256,
)
from .models import (
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    CandidateEvidenceDigest,
    CandidateIsolation,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
)


def build_candidate_evidence(
    *,
    arena_id: str,
    candidate_id: str,
    ordinal: int,
    owner_id: str,
    workspace_id: str,
    base_revision: str,
    run_id: str,
    session_id: str,
    status: CandidateStatus,
    isolation: CandidateIsolation,
    run_capsule: EvidenceBinding,
    context_manifest: EvidenceBinding,
    change_set: EvidenceBinding,
    cost_lease_id: str,
    cost_receipt: EvidenceBinding,
    gate: DeterministicGateReceipt,
    created_at: str,
) -> CandidateEvidence:
    """Build and self-verify one canonical candidate evidence document."""
    provisional = CandidateEvidence(
        evidence_id="pending",
        arena_id=arena_id,
        candidate_id=candidate_id,
        ordinal=ordinal,
        owner_id=owner_id,
        workspace_id=workspace_id,
        base_revision=base_revision,
        run_id=run_id,
        session_id=session_id,
        status=status,
        isolation=isolation,
        run_capsule=run_capsule,
        context_manifest=context_manifest,
        change_set=change_set,
        cost_lease_id=cost_lease_id,
        cost_receipt=cost_receipt,
        gate=gate,
        created_at=created_at,
        evidence_sha256="0" * 64,
    )
    payload = candidate_evidence_to_dict(provisional)
    payload.pop("evidence_id")
    payload.pop("evidence_sha256")
    digest = canonical_sha256(payload)
    value = replace(
        provisional,
        evidence_id=f"arena_candidate_{digest[:24]}",
        evidence_sha256=digest,
    )
    return CandidateEvidence.from_dict(value.to_dict())


def build_arbitration_receipt(
    *,
    arena_id: str,
    owner_id: str,
    workspace_id: str,
    base_revision: str,
    outcome: ArenaOutcome,
    candidates: Iterable[CandidateEvidence],
    selected_candidate_id: str | None,
    reviewer_evidence: EvidenceBinding | None,
    reason_code: str,
    created_at: str,
) -> ArbitrationReceipt:
    """Build a closed outcome over exactly two same-scope candidates."""
    evidence = tuple(sorted(candidates, key=lambda item: item.ordinal))
    if len(evidence) != 2:
        raise ValueError("Reviewed Arena requires exactly two candidates")
    for item in evidence:
        CandidateEvidence.from_dict(item.to_dict())
        if (
            item.arena_id != arena_id
            or item.owner_id != owner_id
            or item.workspace_id != workspace_id
            or item.base_revision != base_revision
        ):
            raise ValueError("candidate evidence crosses the Arena binding")
    digests = tuple(
        CandidateEvidenceDigest(
            candidate_id=item.candidate_id,
            ordinal=item.ordinal,
            evidence_sha256=item.evidence_sha256,
        )
        for item in evidence
    )
    provisional = ArbitrationReceipt(
        receipt_id="pending",
        arena_id=arena_id,
        owner_id=owner_id,
        workspace_id=workspace_id,
        base_revision=base_revision,
        outcome=outcome,
        candidates=(digests[0], digests[1]),
        selected_candidate_id=selected_candidate_id,
        reviewer_evidence=reviewer_evidence,
        reason_code=reason_code,
        created_at=created_at,
        automatic_apply=False,
        receipt_sha256="0" * 64,
    )
    payload = arbitration_receipt_to_dict(provisional)
    payload.pop("receipt_id")
    payload.pop("receipt_sha256")
    digest = canonical_sha256(payload)
    value = replace(
        provisional,
        receipt_id=f"arena_receipt_{digest[:24]}",
        receipt_sha256=digest,
    )
    return ArbitrationReceipt.from_dict(value.to_dict())


__all__ = ["build_arbitration_receipt", "build_candidate_evidence"]
