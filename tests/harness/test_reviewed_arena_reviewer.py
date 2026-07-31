"""Deterministic eligibility and one immutable-evidence reviewer."""

from __future__ import annotations

from dataclasses import replace
import json

from gigaloom.review.arena.api import (
    ArenaOutcome,
    CandidateIsolation,
    CandidateScore,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
    GateOutcome,
    ReviewerDecision,
    ReviewerVerdict,
    build_candidate_evidence,
    review_candidates,
)


NOW = "2026-07-31T09:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class _Reviewer:
    def __init__(
        self,
        *,
        scores: tuple[int, ...] = (8000, 7000),
        selected: str | None = "candidate-1",
        decision: ReviewerDecision = ReviewerDecision.SELECTED,
        fail: bool = False,
    ) -> None:
        self.scores = scores
        self.selected = selected
        self.decision = decision
        self.fail = fail
        self.calls = []

    def review(self, request):
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("reviewer unavailable")
        eligible = [item for item in request.eligibility if item.eligible]
        return ReviewerVerdict(
            reviewer_id="reviewer-1",
            candidate_set_sha256=request.candidate_set_sha256,
            decision=self.decision,
            scores=tuple(
                CandidateScore(item.candidate_id, score)
                for item, score in zip(eligible, self.scores, strict=False)
            ),
            selected_candidate_id=self.selected,
            rationale_sha256=SHA_D,
        )


def test_gate_failure_is_ineligible_before_single_reviewer_ranking(tmp_path) -> None:
    first = _candidate(1, gate=GateOutcome.FAILED)
    second = _candidate(2)
    reviewer = _Reviewer(scores=(9000,), selected="candidate-2")

    result = review_candidates(
        candidates=(first, second),
        evidence_dir=tmp_path / "evidence",
        reviewer=reviewer,
        clock=lambda: NOW,
    )

    assert [item.eligible for item in result.eligibility] == [False, True]
    assert result.eligibility[0].reason_codes == ("gate_failed",)
    assert len(reviewer.calls) == 1
    assert [item.candidate_id for item in reviewer.calls[0].candidate_files] == [
        "candidate-1",
        "candidate-2",
    ]
    assert result.receipt.outcome is ArenaOutcome.SELECTED
    assert result.receipt.selected_candidate_id == "candidate-2"
    assert result.receipt.automatic_apply is False
    for reference in reviewer.calls[0].candidate_files:
        payload = json.loads(reference.path.read_text())
        assert payload["candidate_id"] == reference.candidate_id


def test_both_gate_failures_close_without_calling_reviewer(tmp_path) -> None:
    reviewer = _Reviewer()

    result = review_candidates(
        candidates=(
            _candidate(1, gate=GateOutcome.FAILED),
            _candidate(2, gate=GateOutcome.ERROR),
        ),
        evidence_dir=tmp_path / "unused",
        reviewer=reviewer,
        clock=lambda: NOW,
    )

    assert reviewer.calls == []
    assert result.receipt.outcome is ArenaOutcome.NO_ELIGIBLE_CANDIDATE
    assert result.reviewer_verdict is None


def test_tie_and_explicit_insufficient_evidence_require_a_human(tmp_path) -> None:
    tie_reviewer = _Reviewer(scores=(7000, 7000), selected="candidate-1")
    tied = review_candidates(
        candidates=(_candidate(1), _candidate(2)),
        evidence_dir=tmp_path / "tie",
        reviewer=tie_reviewer,
        clock=lambda: NOW,
    )
    assert tied.receipt.outcome is ArenaOutcome.NEEDS_HUMAN
    assert tied.receipt.selected_candidate_id is None
    assert tied.receipt.reason_code == "reviewer_score_tie"

    human_reviewer = _Reviewer(
        selected=None,
        decision=ReviewerDecision.NEEDS_HUMAN,
    )
    human = review_candidates(
        candidates=(_candidate(1), _candidate(2)),
        evidence_dir=tmp_path / "human",
        reviewer=human_reviewer,
        clock=lambda: NOW,
    )
    assert human.receipt.outcome is ArenaOutcome.NEEDS_HUMAN
    assert human.receipt.reason_code == "reviewer_needs_human"


def test_reviewer_failure_or_ineligible_selection_never_selects(tmp_path) -> None:
    failed = review_candidates(
        candidates=(_candidate(1), _candidate(2)),
        evidence_dir=tmp_path / "failed",
        reviewer=_Reviewer(fail=True),
        clock=lambda: NOW,
    )
    assert failed.receipt.outcome is ArenaOutcome.REVIEW_FAILED
    assert failed.receipt.selected_candidate_id is None

    malformed = review_candidates(
        candidates=(
            _candidate(1, gate=GateOutcome.FAILED),
            _candidate(2),
        ),
        evidence_dir=tmp_path / "malformed",
        reviewer=_Reviewer(scores=(9000,), selected="candidate-1"),
        clock=lambda: NOW,
    )
    assert malformed.receipt.outcome is ArenaOutcome.REVIEW_FAILED
    assert malformed.receipt.selected_candidate_id is None


def test_both_canceled_produce_canceled_outcome_without_reviewer(tmp_path) -> None:
    reviewer = _Reviewer()
    result = review_candidates(
        candidates=(
            _candidate(1, status=CandidateStatus.CANCELED),
            _candidate(2, status=CandidateStatus.CANCELED),
        ),
        evidence_dir=tmp_path / "unused",
        reviewer=reviewer,
        clock=lambda: NOW,
    )

    assert reviewer.calls == []
    assert result.receipt.outcome is ArenaOutcome.CANCELED


def _candidate(
    ordinal: int,
    *,
    gate: GateOutcome = GateOutcome.PASSED,
    status: CandidateStatus = CandidateStatus.SUCCEEDED,
):
    revision = f"candidate-rev-{ordinal}"
    return build_candidate_evidence(
        arena_id="arena-1",
        candidate_id=f"candidate-{ordinal}",
        ordinal=ordinal,
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        run_id=f"run-{ordinal}",
        session_id=f"session-{ordinal}",
        status=status,
        isolation=CandidateIsolation(
            worktree_id=f"worktree-{ordinal}",
            native_home_id=f"native-home-{ordinal}",
            terminal_id=f"terminal-{ordinal}",
            provider_session_id=f"provider-session-{ordinal}",
        ),
        run_capsule=_binding(f"capsule-{ordinal}", SHA_A),
        context_manifest=_binding(f"context-{ordinal}", SHA_B),
        change_set=replace(
            _binding(f"change-{ordinal}", SHA_C),
            revision=revision,
        ),
        cost_lease_id=f"lease-{ordinal}",
        cost_receipt=_binding(f"cost-{ordinal}", SHA_D),
        gate=DeterministicGateReceipt(
            gate_id=f"gate-{ordinal}",
            command_sha256=SHA_A,
            result_sha256=SHA_B,
            checked_revision=revision,
            outcome=gate,
            completed_at=NOW,
        ),
        created_at=NOW,
    )


def _binding(name: str, digest: str) -> EvidenceBinding:
    return EvidenceBinding(
        authority=f"owner.{name}",
        resource_id=f"{name}-1",
        revision=f"{name}-rev-1",
        sha256=digest,
    )
