"""Exactly-two isolation and budget admission for Reviewed Arena."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from threading import Barrier

import pytest

from gigaloom.contracts import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetPolicy,
    CostConfidence,
    CostObservation,
)
from gigaloom.review.arena.api import (
    ArenaIsolationError,
    CandidateIsolation,
    CandidateLaunchSpec,
    CandidatePreparationRequest,
    CandidateRunResult,
    CandidateStatus,
    PreparedCandidate,
    ReviewedArenaExecutionRequest,
    run_reviewed_arena,
)
from gigaloom.runtime.api import BudgetHeadroomExceededError, BudgetLeaseStore


T0 = "2026-07-31T08:00:00Z"
T1 = "2026-07-31T08:01:00Z"
T9 = "2026-07-31T08:09:00Z"
T10 = "2026-07-31T08:10:00Z"
PRICE = "a" * 64


class _Cancellation:
    def __init__(self, canceled: bool = False) -> None:
        self.canceled = canceled

    def is_canceled(self) -> bool:
        return self.canceled


class _Executor:
    def __init__(self, store: BudgetLeaseStore, *, duplicate_worktree: bool = False):
        self.store = store
        self.duplicate_worktree = duplicate_worktree
        self.prepared: list[str] = []
        self.started: list[str] = []
        self.canceled: list[tuple[str, str]] = []
        self.barrier = Barrier(2)
        self.both_leases_visible: list[bool] = []

    def prepare(self, request: CandidatePreparationRequest) -> PreparedCandidate:
        self.prepared.append(request.candidate_id)
        suffix = 1 if self.duplicate_worktree else request.ordinal
        return PreparedCandidate(
            candidate_id=request.candidate_id,
            ordinal=request.ordinal,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            base_revision=request.base_revision,
            preparation_id=f"preparation-{request.ordinal}",
            isolation=CandidateIsolation(
                worktree_id=f"worktree-{suffix}",
                native_home_id=f"native-home-{request.ordinal}",
                terminal_id=f"terminal-{request.ordinal}",
                provider_session_id=f"provider-session-{request.ordinal}",
            ),
        )

    def run(self, prepared, lease, cancellation) -> CandidateRunResult:
        self.started.append(prepared.candidate_id)
        self.both_leases_visible.append(
            len(self.store.list_children(lease.parent_lease_id or "")) == 2
        )
        self.barrier.wait(timeout=5)
        return CandidateRunResult(
            candidate_id=prepared.candidate_id,
            status=CandidateStatus.SUCCEEDED,
            run_id=f"run-{prepared.ordinal}",
            session_id=f"session-{prepared.ordinal}",
            final_cost=_cost(str(prepared.ordinal), observed_at=T1),
            reason_code="candidate_succeeded",
        )

    def cancel(self, prepared: PreparedCandidate, *, reason_code: str) -> None:
        self.canceled.append((prepared.candidate_id, reason_code))


def test_exactly_two_candidates_reserve_both_leases_before_concurrent_spawn(
    tmp_path,
) -> None:
    current = [T0]
    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=_admission("parent-admission", "10"),
            expires_at=T10,
        )
        executor = _Executor(store)

        result = run_reviewed_arena(
            request=_request(parent.lease.id),
            cost_store=store,
            executor=executor,
            clock=lambda: current[0],
        )

        assert executor.prepared == ["candidate-1", "candidate-2"]
        assert set(executor.started) == {"candidate-1", "candidate-2"}
        assert executor.both_leases_visible == [True, True]
        assert [item.result.status for item in result.candidates] == [
            CandidateStatus.SUCCEEDED,
            CandidateStatus.SUCCEEDED,
        ]
        assert [item.cost_receipt.final_cost.amount for item in result.candidates] == [
            Decimal("1"),
            Decimal("2"),
        ]
        parent_after = store.get_balance(parent.lease.id)
        assert parent_after.reserved_amount == Decimal("0")
        assert parent_after.spent_amount == Decimal("3")


def test_atomic_lease_denial_happens_before_any_resource_preparation(tmp_path) -> None:
    current = [T0]
    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="small-parent",
            admission=_admission("small-parent-admission", "3"),
            expires_at=T10,
        )
        executor = _Executor(store)

        with pytest.raises(BudgetHeadroomExceededError, match="headroom"):
            run_reviewed_arena(
                request=_request(
                    parent.lease.id,
                    policy=BudgetPolicy.finite("USD", Decimal("3")),
                ),
                cost_store=store,
                executor=executor,
                clock=lambda: current[0],
            )

        assert executor.prepared == []
        assert executor.started == []
        assert store.list_children(parent.lease.id) == ()


def test_duplicate_isolation_fails_before_spawn_and_closes_both_leases(
    tmp_path,
) -> None:
    current = [T0]
    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=_admission("parent-admission", "10"),
            expires_at=T10,
        )
        executor = _Executor(store, duplicate_worktree=True)

        with pytest.raises(ArenaIsolationError, match="worktree_id"):
            run_reviewed_arena(
                request=_request(parent.lease.id),
                cost_store=store,
                executor=executor,
                clock=lambda: current[0],
            )

        assert executor.started == []
        assert len(executor.canceled) == 2
        children = store.list_children(parent.lease.id)
        assert all(store.get_receipt(item.lease.id) for item in children)
        assert store.get_balance(parent.lease.id).reserved_amount == Decimal("0")


def test_cancellation_before_spawn_cancels_both_and_records_unknown_not_zero(
    tmp_path,
) -> None:
    current = [T0]
    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=_admission("parent-admission", "10"),
            expires_at=T10,
        )
        executor = _Executor(store)

        result = run_reviewed_arena(
            request=_request(parent.lease.id),
            cost_store=store,
            executor=executor,
            clock=lambda: current[0],
            cancellation=_Cancellation(canceled=True),
        )

        assert executor.started == []
        assert len(executor.canceled) == 2
        assert all(
            item.result.final_cost.confidence is CostConfidence.UNKNOWN
            and item.result.final_cost.amount is None
            for item in result.candidates
        )
        assert store.get_balance(parent.lease.id).spent_amount == Decimal("4")


def _request(
    parent_lease_id: str,
    *,
    policy: BudgetPolicy | None = None,
) -> ReviewedArenaExecutionRequest:
    active_policy = policy or BudgetPolicy.finite("USD", Decimal("10"))
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
                admission=_admission("child-admission-1", "2", policy=active_policy),
                lease_id="child-lease-1",
                lease_expires_at=T9,
            ),
            CandidateLaunchSpec(
                candidate_id="candidate-2",
                ordinal=2,
                admission=_admission("child-admission-2", "2", policy=active_policy),
                lease_id="child-lease-2",
                lease_expires_at=T9,
            ),
        ),
    )


def _admission(
    admission_id: str,
    amount: str,
    *,
    policy: BudgetPolicy | None = None,
) -> BudgetAdmission:
    active_policy = policy or BudgetPolicy.finite("USD", Decimal(amount))
    return BudgetAdmission(
        id=admission_id,
        policy=active_policy,
        decision=BudgetAdmissionDecision.ADMITTED,
        route_class="priced_api",
        requested_cost=_cost(amount),
        reason_code="within_headroom",
        created_at=T0,
        price_source_digest=PRICE,
    )


def _cost(amount: str, *, observed_at: str = T0) -> CostObservation:
    return CostObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class="priced_api",
        confidence=CostConfidence.EXACT,
        source="price_catalog",
        source_digest=PRICE,
        observed_at=observed_at,
        currency="USD",
        amount=Decimal(amount),
    )


def _store(tmp_path, current: list[str]) -> BudgetLeaseStore:
    return BudgetLeaseStore(
        tmp_path,
        clock=lambda: datetime.fromisoformat(current[0].replace("Z", "+00:00")),
    )
