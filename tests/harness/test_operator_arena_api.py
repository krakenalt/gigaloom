"""Owner-backed Reviewed Arena operator API."""

from __future__ import annotations

from dataclasses import replace
import json

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.review.arena.api import (
    ArenaOutcome,
    CandidateIsolation,
    CandidateScore,
    CandidateStatus,
    DeterministicGateReceipt,
    EligibilityDecision,
    EvidenceBinding,
    GateOutcome,
    ReviewerDecision,
    ReviewerVerdict,
    WinnerReviewCommand,
    WinnerReviewPreview,
    build_arbitration_receipt,
    build_candidate_evidence,
    handoff_winner,
)
from gigaloom.ui.app import create_app
from gigaloom.ui.services.operator_arena import (
    ReviewWinnerCommand,
    ReviewedArenaConflictError,
    ReviewedArenaProjection,
)


NOW = "2026-07-31T12:00:00Z"
OWNER = "local_operator"
WORKSPACE = "workspace-1"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class _ReviewFlow:
    def prepare_review(self, request):
        return WinnerReviewPreview(
            run_id=request.run_id,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            base_revision=request.base_revision,
            candidate_evidence_sha256=request.candidate_evidence_sha256,
            review_binding=_binding("existing-review", SHA_D),
            ready=True,
            blocking_reason_codes=(),
            automatic_apply=False,
        )


class _Owner:
    def __init__(self) -> None:
        self.projection = _projection()
        self.commands: list[ReviewWinnerCommand] = []
        self.responses = {}

    def get_reviewed_arena(self, *, arena_id, owner_id, workspace_id):
        if arena_id != "arena-1":
            raise KeyError(arena_id)
        if owner_id != OWNER or workspace_id != WORKSPACE:
            raise PermissionError("cross-scope Arena read")
        return self.projection

    def review_winner(self, command):
        if command.owner_id != OWNER or command.workspace_id != WORKSPACE:
            raise PermissionError("cross-scope Arena command")
        if (
            command.arbitration_receipt_sha256
            != self.projection.arbitration.receipt_sha256
            or command.candidate_evidence_sha256
            != self.projection.candidates[1].evidence_sha256
        ):
            raise ReviewedArenaConflictError("stale Arena command")
        self.commands.append(command)
        existing = self.responses.get(command.idempotency_key)
        if existing is not None:
            return existing
        handoff = handoff_winner(
            arbitration=self.projection.arbitration,
            candidates=self.projection.candidates,
            review_flow=_ReviewFlow(),
            clock=lambda: NOW,
        )
        self.responses[command.idempotency_key] = handoff
        return handoff


def test_reviewed_arena_projection_is_exact_bounded_and_content_free(
    tmp_path,
) -> None:
    owner = _Owner()
    client = _client(tmp_path, owner)

    response = client.get(
        "/api/operator/arenas/arena-1/reviewed",
        params={"workspace_id": WORKSPACE},
    )

    assert response.status_code == 200
    arena = response.json()["arena"]
    assert set(arena) == {
        "schema_version",
        "kind",
        "arena_id",
        "owner_id",
        "workspace_id",
        "candidates",
        "eligibility",
        "arbitration",
        "reviewer_verdict",
        "handoff",
        "allowed_commands",
        "projection_sha256",
    }
    assert len(arena["candidates"]) == 2
    assert arena["allowed_commands"] == ["review_winner"]
    assert arena["arbitration"]["automatic_apply"] is False
    assert arena["handoff"] is None
    serialized = json.dumps(arena, sort_keys=True)
    for forbidden in (
        "prompt",
        "response",
        "socket_path",
        "native_home_path",
        "worktree_path",
        "credential",
        "terminal_output",
    ):
        assert forbidden not in serialized
    assert (
        client.get(
            "/api/operator/arenas/arena-1/reviewed",
            params={"workspace_id": "other-workspace"},
        ).status_code
        == 403
    )


def test_review_winner_is_digest_bound_idempotent_and_never_applies(
    tmp_path,
) -> None:
    owner = _Owner()
    client = _client(tmp_path, owner)
    payload = {
        "workspace_id": WORKSPACE,
        "arbitration_receipt_sha256": (owner.projection.arbitration.receipt_sha256),
        "candidate_evidence_sha256": (owner.projection.candidates[1].evidence_sha256),
        "idempotency_key": "review-winner-1",
    }

    first = client.post(
        "/api/operator/arenas/arena-1/review-winner",
        json=payload,
    )
    replay = client.post(
        "/api/operator/arenas/arena-1/review-winner",
        json=payload,
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    handoff = first.json()["handoff"]
    assert handoff["allowed_command"] == "review_winner"
    assert handoff["automatic_apply"] is False
    assert len(owner.responses) == 1
    assert (
        client.post(
            "/api/operator/arenas/arena-1/review-winner",
            json={**payload, "candidate_evidence_sha256": "f" * 64},
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/operator/arenas/arena-1/review-winner",
            json={**payload, "apply": True},
        ).status_code
        == 422
    )


def _client(tmp_path, owner: _Owner) -> TestClient:
    return TestClient(
        create_app(
            HarnessConfig(data_dir=tmp_path),
            reviewed_arena_owner=owner,
        )
    )


def _projection() -> ReviewedArenaProjection:
    candidates = (_candidate(1), _candidate(2))
    arbitration = build_arbitration_receipt(
        arena_id="arena-1",
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        base_revision="1" * 40,
        outcome=ArenaOutcome.SELECTED,
        candidates=candidates,
        selected_candidate_id=candidates[1].candidate_id,
        reviewer_evidence=_binding("reviewer", SHA_D),
        reason_code="reviewer_selected",
        created_at=NOW,
    )
    eligibility = (
        EligibilityDecision("candidate-1", 1, True, ("eligible",)),
        EligibilityDecision("candidate-2", 2, True, ("eligible",)),
    )
    verdict = ReviewerVerdict(
        reviewer_id="reviewer-1",
        candidate_set_sha256=SHA_A,
        decision=ReviewerDecision.SELECTED,
        scores=(
            CandidateScore("candidate-1", 4000),
            CandidateScore("candidate-2", 8000),
        ),
        selected_candidate_id="candidate-2",
        rationale_sha256=SHA_B,
    )
    return ReviewedArenaProjection(
        arena_id="arena-1",
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        candidates=candidates,
        eligibility=eligibility,
        arbitration=arbitration,
        reviewer_verdict=verdict,
        handoff=None,
        allowed_commands=(WinnerReviewCommand.REVIEW_WINNER,),
    )


def _candidate(ordinal: int):
    revision = f"candidate-rev-{ordinal}"
    return build_candidate_evidence(
        arena_id="arena-1",
        candidate_id=f"candidate-{ordinal}",
        ordinal=ordinal,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
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
