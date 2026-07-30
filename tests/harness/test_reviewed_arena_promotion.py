"""Manual-only Reviewed Arena winner handoff."""

from __future__ import annotations

from dataclasses import replace

import pytest

from gigaloom.review.arena.api import (
    ArenaOutcome,
    CandidateIsolation,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
    GateOutcome,
    ReviewWinnerHandoff,
    WinnerHandoffStatus,
    WinnerPromotionError,
    WinnerReviewPreview,
    build_arbitration_receipt,
    build_candidate_evidence,
    handoff_winner,
)


NOW = "2026-07-31T10:00:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class _ReviewFlow:
    def __init__(
        self,
        *,
        ready: bool = True,
        blockers: tuple[str, ...] = (),
        automatic_apply: bool = False,
    ) -> None:
        self.ready = ready
        self.blockers = blockers
        self.automatic_apply = automatic_apply
        self.requests = []

    def prepare_review(self, request):
        self.requests.append(request)
        return WinnerReviewPreview(
            run_id=request.run_id,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            base_revision=request.base_revision,
            candidate_evidence_sha256=request.candidate_evidence_sha256,
            review_binding=_binding("existing-review-flow", SHA_D),
            ready=self.ready,
            blocking_reason_codes=self.blockers,
            automatic_apply=self.automatic_apply,
        )


def test_selected_winner_enters_preview_only_manual_review_flow() -> None:
    candidates = (_candidate(1), _candidate(2))
    arbitration = _selected(candidates, selected="candidate-2")
    flow = _ReviewFlow()

    handoff = handoff_winner(
        arbitration=arbitration,
        candidates=reversed(candidates),
        review_flow=flow,
        clock=lambda: NOW,
    )

    assert len(flow.requests) == 1
    assert handoff.status is WinnerHandoffStatus.READY
    assert handoff.selected_candidate_id == "candidate-2"
    assert handoff.selected_run_id == "run-2"
    assert handoff.allowed_command.value == "review_winner"
    assert handoff.automatic_apply is False
    assert ReviewWinnerHandoff.from_dict(handoff.to_dict()) == handoff
    assert "apply" not in handoff.to_dict()


@pytest.mark.parametrize(
    "blocker",
    ["stale_base", "protected_path", "dirty_destination"],
)
def test_existing_review_flow_blockers_remain_visible_without_apply(
    blocker: str,
) -> None:
    candidates = (_candidate(1), _candidate(2))
    handoff = handoff_winner(
        arbitration=_selected(candidates, selected="candidate-1"),
        candidates=candidates,
        review_flow=_ReviewFlow(ready=False, blockers=(blocker,)),
        clock=lambda: NOW,
    )

    assert handoff.status is WinnerHandoffStatus.BLOCKED
    assert handoff.blocking_reason_codes == (blocker,)
    assert handoff.automatic_apply is False


def test_non_selected_or_stale_arbitration_never_enters_review() -> None:
    candidates = (_candidate(1), _candidate(2))
    flow = _ReviewFlow()
    needs_human = build_arbitration_receipt(
        arena_id="arena-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        outcome=ArenaOutcome.NEEDS_HUMAN,
        candidates=candidates,
        selected_candidate_id=None,
        reviewer_evidence=_binding("reviewer", SHA_D),
        reason_code="tie",
        created_at=NOW,
    )

    with pytest.raises(WinnerPromotionError, match="selected"):
        handoff_winner(
            arbitration=needs_human,
            candidates=candidates,
            review_flow=flow,
            clock=lambda: NOW,
        )
    with pytest.raises(WinnerPromotionError, match="no longer matches"):
        handoff_winner(
            arbitration=_selected(candidates, selected="candidate-1"),
            candidates=(
                replace(candidates[0], evidence_sha256="f" * 64),
                candidates[1],
            ),
            review_flow=flow,
            clock=lambda: NOW,
        )
    assert flow.requests == []


def test_review_owner_cannot_request_automatic_apply() -> None:
    candidates = (_candidate(1), _candidate(2))

    with pytest.raises(WinnerPromotionError, match="automatic apply"):
        handoff_winner(
            arbitration=_selected(candidates, selected="candidate-1"),
            candidates=candidates,
            review_flow=_ReviewFlow(automatic_apply=True),
            clock=lambda: NOW,
        )


def test_handoff_parser_rejects_tampering_and_extra_apply_field() -> None:
    candidates = (_candidate(1), _candidate(2))
    handoff = handoff_winner(
        arbitration=_selected(candidates, selected="candidate-1"),
        candidates=candidates,
        review_flow=_ReviewFlow(),
        clock=lambda: NOW,
    )
    payload = handoff.to_dict()

    with pytest.raises(ValueError, match="unknown or missing"):
        ReviewWinnerHandoff.from_dict({**payload, "apply": True})
    with pytest.raises(ValueError, match="digest"):
        ReviewWinnerHandoff.from_dict({**payload, "selected_run_id": "other-run"})


def _selected(candidates, *, selected: str):
    return build_arbitration_receipt(
        arena_id="arena-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        outcome=ArenaOutcome.SELECTED,
        candidates=candidates,
        selected_candidate_id=selected,
        reviewer_evidence=_binding("reviewer", SHA_D),
        reason_code="reviewer_selected",
        created_at=NOW,
    )


def _candidate(ordinal: int):
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
        status=CandidateStatus.SUCCEEDED,
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
            outcome=GateOutcome.PASSED,
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
