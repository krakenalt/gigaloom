"""Cross-slice failure matrix for the bounded Reviewed Arena."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from gigaloom.contracts import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetPolicy,
    CostConfidence,
    CostObservation,
)
from gigaloom.review.arena.api import (
    ArenaOutcome,
    CandidateIsolation,
    CandidateLaunchSpec,
    CandidatePreparationRequest,
    CandidateRunResult,
    CandidateScore,
    CandidateStatus,
    DeterministicGateReceipt,
    EvidenceBinding,
    GateOutcome,
    PreparedCandidate,
    ReviewedArenaExecutionRequest,
    ReviewerDecision,
    ReviewerVerdict,
    WinnerHandoffStatus,
    WinnerReviewPreview,
    build_candidate_evidence,
    handoff_winner,
    review_candidates,
    run_reviewed_arena,
)
from gigaloom.runtime.api import (
    AdmissionDeniedError,
    BudgetLeaseStore,
    evaluate_budget_admission,
)


T0 = "2026-07-31T11:00:00Z"
T1 = "2026-07-31T11:01:00Z"
T9 = "2026-07-31T11:09:00Z"
T10 = "2026-07-31T11:10:00Z"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


class _Executor:
    def __init__(self, *, fail: frozenset[int] = frozenset()) -> None:
        self.fail = fail
        self.prepared: list[int] = []
        self.started: list[int] = []
        self.canceled: list[int] = []

    def prepare(self, request: CandidatePreparationRequest) -> PreparedCandidate:
        self.prepared.append(request.ordinal)
        return PreparedCandidate(
            candidate_id=request.candidate_id,
            ordinal=request.ordinal,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            base_revision=request.base_revision,
            preparation_id=f"preparation-{request.ordinal}",
            isolation=CandidateIsolation(
                worktree_id=f"worktree-{request.ordinal}",
                native_home_id=f"native-home-{request.ordinal}",
                terminal_id=f"terminal-{request.ordinal}",
                provider_session_id=f"provider-session-{request.ordinal}",
            ),
        )

    def run(self, prepared, lease, cancellation) -> CandidateRunResult:
        self.started.append(prepared.ordinal)
        if prepared.ordinal in self.fail:
            raise RuntimeError("candidate failed")
        return CandidateRunResult(
            candidate_id=prepared.candidate_id,
            status=CandidateStatus.SUCCEEDED,
            run_id=f"run-{prepared.ordinal}",
            session_id=f"session-{prepared.ordinal}",
            final_cost=_known_cost("1", observed_at=T1),
            reason_code="candidate_succeeded",
        )

    def cancel(self, prepared: PreparedCandidate, *, reason_code: str) -> None:
        self.canceled.append(prepared.ordinal)


class _Reviewer:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def review(self, request):
        self.calls += 1
        if self.mode == "raises":
            raise RuntimeError("review unavailable")
        eligible = tuple(item for item in request.eligibility if item.eligible)
        scores = tuple(
            CandidateScore(
                candidate_id=item.candidate_id,
                score_basis_points=(
                    7000 if self.mode == "tie" else 9000 - (index * 1000)
                ),
            )
            for index, item in enumerate(eligible)
        )
        return ReviewerVerdict(
            reviewer_id="reviewer-1",
            candidate_set_sha256=(
                "f" * 64 if self.mode == "stale" else request.candidate_set_sha256
            ),
            decision=ReviewerDecision.SELECTED,
            scores=scores,
            selected_candidate_id=eligible[0].candidate_id,
            rationale_sha256=SHA_D,
        )


class _BlockedReviewFlow:
    def __init__(self, blocker: str) -> None:
        self.blocker = blocker

    def prepare_review(self, request):
        return WinnerReviewPreview(
            run_id=request.run_id,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            base_revision=request.base_revision,
            candidate_evidence_sha256=request.candidate_evidence_sha256,
            review_binding=_binding("review-preview", SHA_D),
            ready=False,
            blocking_reason_codes=(self.blocker,),
            automatic_apply=False,
        )


def test_one_execution_failure_is_ineligible_and_other_can_be_reviewed(
    tmp_path,
) -> None:
    current = [T0]
    executor = _Executor(fail=frozenset({1}))
    with _store(tmp_path / "cost", current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=_admission("parent-admission", "10"),
            expires_at=T10,
        )
        execution = run_reviewed_arena(
            request=_request(parent.lease.id),
            cost_store=store,
            executor=executor,
            clock=lambda: current[0],
        )

        assert [item.result.status for item in execution.candidates] == [
            CandidateStatus.FAILED,
            CandidateStatus.SUCCEEDED,
        ]
        assert (
            execution.candidates[0].result.final_cost.confidence
            is CostConfidence.UNKNOWN
        )
        assert execution.candidates[0].result.final_cost.amount is None
        assert store.get_balance(parent.lease.id).spent_amount == Decimal("3")

        evidence = tuple(
            _evidence_from_execution(
                item,
                gate=(
                    GateOutcome.ERROR
                    if item.result.status is CandidateStatus.FAILED
                    else GateOutcome.PASSED
                ),
            )
            for item in execution.candidates
        )
        reviewed = review_candidates(
            candidates=evidence,
            evidence_dir=tmp_path / "evidence",
            reviewer=_Reviewer("selected"),
            clock=lambda: T1,
        )

    assert [item.eligible for item in reviewed.eligibility] == [False, True]
    assert reviewed.receipt.outcome is ArenaOutcome.SELECTED
    assert reviewed.receipt.selected_candidate_id == "candidate-2"


def test_unknown_cost_under_finite_budget_blocks_both_before_prepare(
    tmp_path,
) -> None:
    current = [T0]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    unknown = CostObservation(
        provider_id="codex",
        model_id="subscription",
        route_class="native_subscription",
        confidence=CostConfidence.UNKNOWN,
        source="native_session",
        source_digest=SHA_A,
        observed_at=T0,
        reason_code="monetary_cost_unavailable",
    )
    denied = evaluate_budget_admission(
        admission_id="unknown-admission",
        policy=policy,
        requested_cost=unknown,
        created_at=T0,
    )
    executor = _Executor()

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=_admission("parent-admission", "10"),
            expires_at=T10,
        )
        request = _request(parent.lease.id)
        request = replace(
            request,
            candidates=(
                replace(request.candidates[0], admission=denied),
                request.candidates[1],
            ),
        )

        with pytest.raises(AdmissionDeniedError, match="unknown_monetary_cost"):
            run_reviewed_arena(
                request=request,
                cost_store=store,
                executor=executor,
                clock=lambda: current[0],
            )

        assert executor.prepared == []
        assert executor.started == []
        assert store.list_children(parent.lease.id) == ()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("tie", ArenaOutcome.NEEDS_HUMAN),
        ("stale", ArenaOutcome.REVIEW_FAILED),
        ("raises", ArenaOutcome.REVIEW_FAILED),
    ],
)
def test_tie_stale_and_failed_reviewer_never_select(
    tmp_path,
    mode: str,
    expected: ArenaOutcome,
) -> None:
    reviewer = _Reviewer(mode)
    result = review_candidates(
        candidates=(_successful_evidence(1), _successful_evidence(2)),
        evidence_dir=tmp_path / mode,
        reviewer=reviewer,
        clock=lambda: T1,
    )

    assert reviewer.calls == 1
    assert result.receipt.outcome is expected
    assert result.receipt.selected_candidate_id is None
    assert result.receipt.automatic_apply is False


@pytest.mark.parametrize(
    "blocker",
    ["stale_base", "protected_path", "dirty_destination"],
)
def test_selected_winner_keeps_existing_review_blockers(
    tmp_path,
    blocker: str,
) -> None:
    candidates = (_successful_evidence(1), _successful_evidence(2))
    reviewed = review_candidates(
        candidates=candidates,
        evidence_dir=tmp_path / blocker,
        reviewer=_Reviewer("selected"),
        clock=lambda: T1,
    )
    handoff = handoff_winner(
        arbitration=reviewed.receipt,
        candidates=candidates,
        review_flow=_BlockedReviewFlow(blocker),
        clock=lambda: T1,
    )

    assert handoff.status is WinnerHandoffStatus.BLOCKED
    assert handoff.blocking_reason_codes == (blocker,)
    assert handoff.automatic_apply is False


def _request(parent_lease_id: str) -> ReviewedArenaExecutionRequest:
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    return ReviewedArenaExecutionRequest(
        arena_id="arena-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        parent_lease_id=parent_lease_id,
        candidates=(
            CandidateLaunchSpec(
                candidate_id="candidate-1",
                ordinal=1,
                admission=_admission("child-admission-1", "2", policy=policy),
                lease_id="child-lease-1",
                lease_expires_at=T9,
            ),
            CandidateLaunchSpec(
                candidate_id="candidate-2",
                ordinal=2,
                admission=_admission("child-admission-2", "2", policy=policy),
                lease_id="child-lease-2",
                lease_expires_at=T9,
            ),
        ),
    )


def _evidence_from_execution(item, *, gate: GateOutcome):
    revision = f"candidate-rev-{item.prepared.ordinal}"
    return build_candidate_evidence(
        arena_id="arena-1",
        candidate_id=item.prepared.candidate_id,
        ordinal=item.prepared.ordinal,
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        run_id=item.result.run_id or f"failed-run-{item.prepared.ordinal}",
        session_id=item.result.session_id or f"failed-session-{item.prepared.ordinal}",
        status=item.result.status,
        isolation=item.prepared.isolation,
        run_capsule=_binding(f"capsule-{item.prepared.ordinal}", SHA_A),
        context_manifest=_binding(f"context-{item.prepared.ordinal}", SHA_B),
        change_set=replace(
            _binding(f"change-{item.prepared.ordinal}", SHA_C),
            revision=revision,
        ),
        cost_lease_id=item.lease.id,
        cost_receipt=_binding(f"cost-{item.prepared.ordinal}", SHA_D),
        gate=DeterministicGateReceipt(
            gate_id=f"gate-{item.prepared.ordinal}",
            command_sha256=SHA_A,
            result_sha256=SHA_B,
            checked_revision=revision,
            outcome=gate,
            completed_at=T1,
        ),
        created_at=T1,
    )


def _successful_evidence(ordinal: int):
    prepared = PreparedCandidate(
        candidate_id=f"candidate-{ordinal}",
        ordinal=ordinal,
        owner_id="owner-1",
        workspace_id="workspace-1",
        base_revision="1" * 40,
        preparation_id=f"preparation-{ordinal}",
        isolation=CandidateIsolation(
            worktree_id=f"worktree-{ordinal}",
            native_home_id=f"native-home-{ordinal}",
            terminal_id=f"terminal-{ordinal}",
            provider_session_id=f"provider-session-{ordinal}",
        ),
    )
    result = CandidateRunResult(
        candidate_id=prepared.candidate_id,
        status=CandidateStatus.SUCCEEDED,
        run_id=f"run-{ordinal}",
        session_id=f"session-{ordinal}",
        final_cost=_known_cost("1", observed_at=T1),
        reason_code="candidate_succeeded",
    )

    class _ExecutionItem:
        pass

    item = _ExecutionItem()
    item.prepared = prepared
    item.result = result
    item.lease = type("_Lease", (), {"id": f"lease-{ordinal}"})()
    return _evidence_from_execution(item, gate=GateOutcome.PASSED)


def _admission(
    admission_id: str,
    amount: str,
    *,
    policy: BudgetPolicy | None = None,
) -> BudgetAdmission:
    return BudgetAdmission(
        id=admission_id,
        policy=policy or BudgetPolicy.finite("USD", Decimal(amount)),
        decision=BudgetAdmissionDecision.ADMITTED,
        route_class="priced_api",
        requested_cost=_known_cost(amount),
        reason_code="within_headroom",
        created_at=T0,
        price_source_digest=SHA_A,
    )


def _known_cost(amount: str, *, observed_at: str = T0) -> CostObservation:
    return CostObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class="priced_api",
        confidence=CostConfidence.EXACT,
        source="price_catalog",
        source_digest=SHA_A,
        observed_at=observed_at,
        currency="USD",
        amount=Decimal(amount),
    )


def _binding(name: str, digest: str) -> EvidenceBinding:
    return EvidenceBinding(
        authority=f"owner.{name}",
        resource_id=f"{name}-1",
        revision=f"{name}-rev-1",
        sha256=digest,
    )


def _store(path, current: list[str]) -> BudgetLeaseStore:
    return BudgetLeaseStore(
        path,
        clock=lambda: datetime.fromisoformat(current[0].replace("Z", "+00:00")),
    )
