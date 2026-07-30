"""Exactly-two Reviewed Arena execution over injected owner boundaries."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
from typing import Callable, Protocol

from gigaloom.contracts import (
    BudgetAdmission,
    BudgetLease,
    CostConfidence,
    CostObservation,
    CostReceipt,
    CostReceiptOutcome,
)
from gigaloom.runtime.api import ChildLeaseRequest

from .models import CandidateIsolation, CandidateStatus


class ArenaExecutionError(RuntimeError):
    """Base error for fail-closed Reviewed Arena execution."""


class ArenaIsolationError(ArenaExecutionError):
    """Raised before spawn when candidate resources are not isolated."""


@dataclass(frozen=True, slots=True)
class CandidateLaunchSpec:
    """One pre-admitted candidate launch request."""

    candidate_id: str
    ordinal: int
    admission: BudgetAdmission
    lease_id: str
    lease_expires_at: str


@dataclass(frozen=True, slots=True)
class ReviewedArenaExecutionRequest:
    """Owner-bound request for exactly two candidate runs."""

    arena_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    parent_lease_id: str
    candidates: tuple[CandidateLaunchSpec, CandidateLaunchSpec]


@dataclass(frozen=True, slots=True)
class CandidatePreparationRequest:
    """Content-free isolation request sent to the execution owner."""

    arena_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    candidate_id: str
    ordinal: int
    lease: BudgetLease


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    """Opaque prepared resources that have not started model execution."""

    candidate_id: str
    ordinal: int
    owner_id: str
    workspace_id: str
    base_revision: str
    preparation_id: str
    isolation: CandidateIsolation


@dataclass(frozen=True, slots=True)
class CandidateRunResult:
    """Terminal owner result with honest final monetary evidence."""

    candidate_id: str
    status: CandidateStatus
    run_id: str | None
    session_id: str | None
    final_cost: CostObservation
    reason_code: str


@dataclass(frozen=True, slots=True)
class ExecutedCandidate:
    """One terminal candidate bound to its lease and receipt."""

    prepared: PreparedCandidate
    lease: BudgetLease
    result: CandidateRunResult
    cost_receipt: CostReceipt


@dataclass(frozen=True, slots=True)
class ReviewedArenaExecution:
    """Terminal result of the exactly-two execution phase."""

    arena_id: str
    owner_id: str
    workspace_id: str
    base_revision: str
    candidates: tuple[ExecutedCandidate, ExecutedCandidate]


class ArenaCancellation(Protocol):
    """Read-only cancellation signal owned by the caller."""

    def is_canceled(self) -> bool:
        """Return whether new or continuing candidate work must stop."""


class CandidateExecutorPort(Protocol):
    """Execution owner that creates and runs isolated candidate resources."""

    def prepare(self, request: CandidatePreparationRequest) -> PreparedCandidate:
        """Prepare resources without starting provider execution."""

    def run(
        self,
        prepared: PreparedCandidate,
        lease: BudgetLease,
        cancellation: ArenaCancellation,
    ) -> CandidateRunResult:
        """Run one already-isolated candidate within its lease."""

    def cancel(self, prepared: PreparedCandidate, *, reason_code: str) -> None:
        """Cancel or reap one prepared candidate idempotently."""


class CostLeasePort(Protocol):
    """Public subset of the runtime cost owner consumed by Arena."""

    def reserve_children(
        self,
        parent_lease_id: str,
        requests: tuple[ChildLeaseRequest, ...],
    ) -> tuple[LeaseBalancePort, ...]:
        """Atomically reserve child leases."""

    def finalize_receipt(
        self,
        *,
        receipt_id: str,
        lease_id: str,
        outcome: CostReceiptOutcome,
        final_cost: CostObservation,
        reason_code: str,
    ) -> ReceiptRecordPort:
        """Close one lease and return a record exposing ``receipt``."""


class LeaseBalancePort(Protocol):
    """Content-free child balance returned by the runtime owner."""

    lease: BudgetLease


class ReceiptRecordPort(Protocol):
    """Terminal record returned by the runtime cost owner."""

    receipt: CostReceipt


class _NeverCanceled:
    def is_canceled(self) -> bool:
        return False


def run_reviewed_arena(
    *,
    request: ReviewedArenaExecutionRequest,
    cost_store: CostLeasePort,
    executor: CandidateExecutorPort,
    clock: Callable[[], str],
    cancellation: ArenaCancellation | None = None,
) -> ReviewedArenaExecution:
    """Reserve both leases, prove isolation, then run exactly two candidates."""
    specs = _validate_request(request)
    signal = cancellation or _NeverCanceled()
    balances = cost_store.reserve_children(
        request.parent_lease_id,
        tuple(
            ChildLeaseRequest(spec.lease_id, spec.admission, spec.lease_expires_at)
            for spec in specs
        ),
    )
    leases = tuple(balance.lease for balance in balances)
    if len(leases) != 2:
        raise ArenaExecutionError("cost owner did not return exactly two leases")
    lease_pair = (leases[0], leases[1])

    prepared: list[PreparedCandidate] = []
    try:
        for spec, lease in zip(specs, lease_pair, strict=True):
            item = executor.prepare(
                CandidatePreparationRequest(
                    arena_id=request.arena_id,
                    owner_id=request.owner_id,
                    workspace_id=request.workspace_id,
                    base_revision=request.base_revision,
                    candidate_id=spec.candidate_id,
                    ordinal=spec.ordinal,
                    lease=lease,
                )
            )
            _validate_prepared(request, spec, item)
            prepared.append(item)
        _require_distinct_isolation(prepared)
    except Exception:
        _cancel_prepared(executor, prepared, reason_code="arena_preparation_failed")
        _close_unstarted_leases(
            request=request,
            specs=specs,
            leases=lease_pair,
            cost_store=cost_store,
            clock=clock,
            reason_code="arena_preparation_failed",
        )
        raise

    if signal.is_canceled():
        _cancel_prepared(executor, prepared, reason_code="arena_canceled_before_spawn")
        results = tuple(
            _canceled_result(spec, clock(), reason_code="arena_canceled_before_spawn")
            for spec in specs
        )
    else:
        results = _run_both(executor, prepared, lease_pair, signal, specs, clock)

    executed = tuple(
        _finalize_candidate(
            request=request,
            prepared=item,
            lease=lease,
            result=result,
            cost_store=cost_store,
        )
        for item, lease, result in zip(prepared, lease_pair, results, strict=True)
    )
    return ReviewedArenaExecution(
        arena_id=request.arena_id,
        owner_id=request.owner_id,
        workspace_id=request.workspace_id,
        base_revision=request.base_revision,
        candidates=(executed[0], executed[1]),
    )


def _run_both(
    executor: CandidateExecutorPort,
    prepared: list[PreparedCandidate],
    leases: tuple[BudgetLease, BudgetLease],
    cancellation: ArenaCancellation,
    specs: tuple[CandidateLaunchSpec, CandidateLaunchSpec],
    clock: Callable[[], str],
) -> tuple[CandidateRunResult, CandidateRunResult]:
    def invoke(index: int) -> CandidateRunResult:
        try:
            result = executor.run(prepared[index], leases[index], cancellation)
            _validate_result(specs[index], result)
            return result
        except Exception:
            executor.cancel(prepared[index], reason_code="candidate_execution_failed")
            return _failed_result(
                specs[index],
                clock(),
                reason_code="candidate_execution_failed",
            )

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="reviewed-arena") as pool:
        futures = (pool.submit(invoke, 0), pool.submit(invoke, 1))
        return (futures[0].result(), futures[1].result())


def _finalize_candidate(
    *,
    request: ReviewedArenaExecutionRequest,
    prepared: PreparedCandidate,
    lease: BudgetLease,
    result: CandidateRunResult,
    cost_store: CostLeasePort,
) -> ExecutedCandidate:
    outcome = {
        CandidateStatus.SUCCEEDED: CostReceiptOutcome.SUCCEEDED,
        CandidateStatus.FAILED: CostReceiptOutcome.FAILED,
        CandidateStatus.CANCELED: CostReceiptOutcome.CANCELED,
    }[result.status]
    record = cost_store.finalize_receipt(
        receipt_id=f"{request.arena_id}:{prepared.candidate_id}:receipt",
        lease_id=lease.id,
        outcome=outcome,
        final_cost=result.final_cost,
        reason_code=result.reason_code,
    )
    receipt = record.receipt
    if not isinstance(receipt, CostReceipt):
        raise ArenaExecutionError("cost owner returned an invalid receipt")
    return ExecutedCandidate(
        prepared=prepared,
        lease=lease,
        result=result,
        cost_receipt=receipt,
    )


def _close_unstarted_leases(
    *,
    request: ReviewedArenaExecutionRequest,
    specs: tuple[CandidateLaunchSpec, CandidateLaunchSpec],
    leases: tuple[BudgetLease, BudgetLease],
    cost_store: CostLeasePort,
    clock: Callable[[], str],
    reason_code: str,
) -> None:
    for spec, lease in zip(specs, leases, strict=True):
        cost_store.finalize_receipt(
            receipt_id=f"{request.arena_id}:{spec.candidate_id}:receipt",
            lease_id=lease.id,
            outcome=CostReceiptOutcome.CANCELED,
            final_cost=_unknown_final_cost(spec, clock(), reason_code=reason_code),
            reason_code=reason_code,
        )


def _validate_request(
    request: ReviewedArenaExecutionRequest,
) -> tuple[CandidateLaunchSpec, CandidateLaunchSpec]:
    specs = tuple(sorted(request.candidates, key=lambda item: item.ordinal))
    if len(specs) != 2 or tuple(item.ordinal for item in specs) != (1, 2):
        raise ValueError("Reviewed Arena requires candidate ordinals 1 and 2")
    if len({item.candidate_id for item in specs}) != 2:
        raise ValueError("Reviewed Arena candidate ids must be distinct")
    if len({item.lease_id for item in specs}) != 2:
        raise ValueError("Reviewed Arena lease ids must be distinct")
    return (specs[0], specs[1])


def _validate_prepared(
    request: ReviewedArenaExecutionRequest,
    spec: CandidateLaunchSpec,
    prepared: PreparedCandidate,
) -> None:
    if (
        prepared.candidate_id != spec.candidate_id
        or prepared.ordinal != spec.ordinal
        or prepared.owner_id != request.owner_id
        or prepared.workspace_id != request.workspace_id
        or prepared.base_revision != request.base_revision
    ):
        raise ArenaIsolationError("prepared candidate crosses its Arena binding")


def _require_distinct_isolation(prepared: list[PreparedCandidate]) -> None:
    if len(prepared) != 2:
        raise ArenaIsolationError("exactly two prepared candidates are required")
    fields = (
        "worktree_id",
        "native_home_id",
        "terminal_id",
        "provider_session_id",
    )
    for field in fields:
        values = {
            getattr(item.isolation, field)
            for item in prepared
            if getattr(item.isolation, field)
        }
        if len(values) != 2:
            raise ArenaIsolationError(f"candidate {field} values must be distinct")


def _validate_result(spec: CandidateLaunchSpec, result: CandidateRunResult) -> None:
    if result.candidate_id != spec.candidate_id:
        raise ArenaExecutionError("candidate result identity does not match launch")
    if result.status is CandidateStatus.SUCCEEDED and (
        result.run_id is None or result.session_id is None
    ):
        raise ArenaExecutionError("successful candidate requires run and session ids")


def _cancel_prepared(
    executor: CandidateExecutorPort,
    prepared: list[PreparedCandidate],
    *,
    reason_code: str,
) -> None:
    for item in prepared:
        try:
            executor.cancel(item, reason_code=reason_code)
        except Exception:
            continue


def _failed_result(
    spec: CandidateLaunchSpec,
    observed_at: str,
    *,
    reason_code: str,
) -> CandidateRunResult:
    return CandidateRunResult(
        candidate_id=spec.candidate_id,
        status=CandidateStatus.FAILED,
        run_id=None,
        session_id=None,
        final_cost=_unknown_final_cost(spec, observed_at, reason_code=reason_code),
        reason_code=reason_code,
    )


def _canceled_result(
    spec: CandidateLaunchSpec,
    observed_at: str,
    *,
    reason_code: str,
) -> CandidateRunResult:
    return CandidateRunResult(
        candidate_id=spec.candidate_id,
        status=CandidateStatus.CANCELED,
        run_id=None,
        session_id=None,
        final_cost=_unknown_final_cost(spec, observed_at, reason_code=reason_code),
        reason_code=reason_code,
    )


def _unknown_final_cost(
    spec: CandidateLaunchSpec,
    observed_at: str,
    *,
    reason_code: str,
) -> CostObservation:
    requested = spec.admission.requested_cost
    source_digest = hashlib.sha256(
        f"{spec.candidate_id}\0{spec.lease_id}\0{reason_code}".encode()
    ).hexdigest()
    return CostObservation(
        provider_id=requested.provider_id,
        model_id=requested.model_id,
        route_class=requested.route_class,
        confidence=CostConfidence.UNKNOWN,
        source="reviewed_arena",
        source_digest=source_digest,
        observed_at=observed_at,
        reason_code=reason_code,
    )


__all__ = [
    "ArenaCancellation",
    "ArenaExecutionError",
    "ArenaIsolationError",
    "CandidateExecutorPort",
    "CandidateLaunchSpec",
    "CandidatePreparationRequest",
    "CandidateRunResult",
    "CostLeasePort",
    "ExecutedCandidate",
    "LeaseBalancePort",
    "PreparedCandidate",
    "ReceiptRecordPort",
    "ReviewedArenaExecution",
    "ReviewedArenaExecutionRequest",
    "run_reviewed_arena",
]
