"""Contracts for immutable Reviewed Arena evidence and outcomes."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json

import pytest

from gigaloom.review.arena.api import (
    ArenaOutcome,
    ArbitrationReceipt,
    CandidateEvidence,
    CandidateIsolation,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
    GateOutcome,
    build_arbitration_receipt,
    build_candidate_evidence,
)


NOW = "2026-07-31T08:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _binding(name: str, digest: str) -> EvidenceBinding:
    return EvidenceBinding(
        authority=f"owner.{name}",
        resource_id=f"{name}-1",
        revision=f"{name}-rev-1",
        sha256=digest,
    )


def _candidate(ordinal: int) -> CandidateEvidence:
    return build_candidate_evidence(
        arena_id="arena-1",
        candidate_id=f"candidate-{ordinal}",
        ordinal=ordinal,
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        run_id=f"run-{ordinal}",
        session_id=f"session-{ordinal}",
        status=CandidateStatus.SUCCEEDED,
        isolation=CandidateIsolation(
            worktree_id=f"worktree-{ordinal}",
            native_home_id=f"native-home-{ordinal}",
            terminal_id=f"terminal-{ordinal}",
            provider_session_id=f"provider-session-{ordinal}",
        ),
        run_capsule=_binding(f"capsule-{ordinal}", SHA_A),
        context_manifest=_binding(f"context-{ordinal}", SHA_B),
        change_set=_binding(f"change-{ordinal}", SHA_C),
        cost_lease_id=f"lease-{ordinal}",
        cost_receipt=_binding(f"cost-{ordinal}", SHA_D),
        gate=DeterministicGateReceipt(
            gate_id=f"gate-{ordinal}",
            command_sha256=SHA_A,
            result_sha256=SHA_B,
            checked_revision=f"candidate-rev-{ordinal}",
            outcome=GateOutcome.PASSED,
            completed_at=NOW,
        ),
        created_at=NOW,
    )


def test_candidate_evidence_round_trips_and_binds_content_free_owners() -> None:
    candidate = _candidate(1)
    payload = candidate.to_dict()

    assert CandidateEvidence.from_dict(payload) == candidate
    assert candidate.evidence_id == f"arena_candidate_{candidate.evidence_sha256[:24]}"
    assert payload["ordinal"] == 1
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in (
        "prompt",
        "response",
        "terminal_output",
        "socket_path",
        "native_home_path",
        "worktree_path",
        "credential",
    ):
        assert forbidden not in serialized
    with pytest.raises(FrozenInstanceError):
        candidate.status = CandidateStatus.FAILED  # type: ignore[misc]


def test_candidate_parser_rejects_tampering_future_fields_and_bad_ordinals() -> None:
    payload = _candidate(1).to_dict()

    tampered = deepcopy(payload)
    tampered["run_id"] = "other-run"
    with pytest.raises(ValueError, match="evidence_sha256"):
        CandidateEvidence.from_dict(tampered)
    with pytest.raises(ValueError, match="unknown or missing fields"):
        CandidateEvidence.from_dict({**payload, "raw_output": "secret"})
    with pytest.raises(ValueError, match="ordinal"):
        CandidateEvidence.from_dict({**payload, "ordinal": 3})


def test_arbitration_receipt_requires_exactly_two_and_never_auto_applies() -> None:
    first = _candidate(1)
    second = _candidate(2)
    receipt = build_arbitration_receipt(
        arena_id="arena-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        outcome=ArenaOutcome.SELECTED,
        candidates=(second, first),
        selected_candidate_id=second.candidate_id,
        reviewer_evidence=_binding("reviewer", SHA_D),
        reason_code="candidate_two_ranked_higher",
        created_at=NOW,
    )

    assert receipt.automatic_apply is False
    assert [item.ordinal for item in receipt.candidates] == [1, 2]
    assert ArbitrationReceipt.from_dict(receipt.to_dict()) == receipt
    assert receipt.receipt_id == f"arena_receipt_{receipt.receipt_sha256[:24]}"

    with pytest.raises(ValueError, match="exactly two"):
        build_arbitration_receipt(
            arena_id="arena-1",
            owner_id="owner-1",
            workspace_id="workspace-1",
            base_revision="1" * 40,
            outcome=ArenaOutcome.NEEDS_HUMAN,
            candidates=(first,),
            selected_candidate_id=None,
            reviewer_evidence=None,
            reason_code="insufficient_evidence",
            created_at=NOW,
        )


def test_arbitration_rejects_cross_scope_selection_and_digest_tampering() -> None:
    first = _candidate(1)
    second = _candidate(2)

    with pytest.raises(ValueError, match="crosses"):
        build_arbitration_receipt(
            arena_id="other-arena",
            owner_id="owner-1",
            workspace_id="workspace-1",
            base_revision="1" * 40,
            outcome=ArenaOutcome.NEEDS_HUMAN,
            candidates=(first, second),
            selected_candidate_id=None,
            reviewer_evidence=None,
            reason_code="tie",
            created_at=NOW,
        )

    receipt = build_arbitration_receipt(
        arena_id="arena-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        outcome=ArenaOutcome.NO_ELIGIBLE_CANDIDATE,
        candidates=(first, second),
        selected_candidate_id=None,
        reviewer_evidence=None,
        reason_code="gates_failed",
        created_at=NOW,
    )
    payload = receipt.to_dict()
    payload["automatic_apply"] = True
    with pytest.raises(ValueError, match="automatic_apply"):
        ArbitrationReceipt.from_dict(payload)
