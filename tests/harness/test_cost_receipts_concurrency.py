"""Final receipt, concurrency, and transaction-budget tests for W5."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
import sqlite3
from threading import Barrier
from time import perf_counter

import pytest

from gigaloom.contracts import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetPolicy,
    CostConfidence,
    CostObservation,
    CostReceiptOutcome,
)
from gigaloom.runtime.api import (
    BudgetHeadroomExceededError,
    BudgetLeaseClosedError,
    BudgetLeaseStore,
    ChildLeaseRequest,
    CostObservationConflictError,
    CostReceiptConflictError,
    evaluate_budget_admission,
)


T0 = "2026-07-30T10:00:00Z"
T1 = "2026-07-30T10:01:00Z"
T2 = "2026-07-30T10:02:00Z"
T8 = "2026-07-30T10:08:00Z"
T9 = "2026-07-30T10:09:00Z"
T10 = "2026-07-30T10:10:00Z"
PRICE_DIGEST = "c" * 64


def test_canceled_unknown_receipt_is_bounded_and_returns_parent_reservation(
    tmp_path,
) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root = _admission("root-admission", policy, _known_cost("1"))
    child = _admission("child-admission", policy, _known_cost("3"))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        (reserved,) = store.reserve_children(
            parent.lease.id,
            (ChildLeaseRequest("child-lease", child, T9),),
        )
        store.record_cumulative_spend(
            reserved.lease.id,
            _known_cost("1", observed_at=T2),
        )

        final_unknown = _unknown_cost(
            observed_at=T8,
            route_class="priced_api",
        )
        current[0] = T8
        record = store.finalize_receipt(
            receipt_id="receipt-canceled",
            lease_id=reserved.lease.id,
            outcome=CostReceiptOutcome.CANCELED,
            final_cost=final_unknown,
            reason_code="child_canceled",
        )
        parent_after = store.get_balance(parent.lease.id)
        child_after = store.get_balance(reserved.lease.id)

        assert record.receipt.final_cost.confidence is CostConfidence.UNKNOWN
        assert record.receipt.final_cost.amount is None
        assert record.accounted_currency == "USD"
        assert record.accounted_amount == Decimal("3")
        assert parent_after.reserved_amount == Decimal("0")
        assert parent_after.spent_amount == Decimal("3")
        assert parent_after.available_amount == Decimal("7")
        assert child_after.spent_amount == Decimal("1")
        assert child_after.closed_at == T8

        with pytest.raises(BudgetLeaseClosedError, match="closed"):
            store.record_cumulative_spend(
                reserved.lease.id,
                _known_cost("1.50", observed_at=T8),
            )

        current[0] = T9
        retried = store.finalize_receipt(
            receipt_id="receipt-canceled",
            lease_id=reserved.lease.id,
            outcome=CostReceiptOutcome.CANCELED,
            final_cost=final_unknown,
            reason_code="child_canceled",
        )
        assert retried == record
        assert retried.receipt.closed_at == T8

        with pytest.raises(CostReceiptConflictError, match="different receipt"):
            store.finalize_receipt(
                receipt_id="receipt-other",
                lease_id=reserved.lease.id,
                outcome=CostReceiptOutcome.CANCELED,
                final_cost=final_unknown,
                reason_code="child_canceled",
            )


def test_failed_known_receipt_charges_actual_cost_and_releases_unused_headroom(
    tmp_path,
) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root = _admission("root-admission", policy, _known_cost("1"))
    child = _admission("child-admission", policy, _known_cost("4"))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        (reserved,) = store.reserve_children(
            parent.lease.id,
            (ChildLeaseRequest("child-lease", child, T9),),
        )
        store.record_cumulative_spend(
            reserved.lease.id,
            _known_cost("1", observed_at=T2),
        )

        with pytest.raises(CostObservationConflictError, match="below cumulative"):
            store.finalize_receipt(
                receipt_id="receipt-too-low",
                lease_id=reserved.lease.id,
                outcome=CostReceiptOutcome.FAILED,
                final_cost=_known_cost("0.99", observed_at=T8),
                reason_code="child_failed",
            )
        assert store.get_balance(parent.lease.id).reserved_amount == Decimal("4")

        current[0] = T8
        record = store.finalize_receipt(
            receipt_id="receipt-failed",
            lease_id=reserved.lease.id,
            outcome=CostReceiptOutcome.FAILED,
            final_cost=_known_cost("1.50", observed_at=T8),
            reason_code="child_failed",
        )
        parent_after = store.get_balance(parent.lease.id)

        assert record.accounted_amount == Decimal("1.5")
        assert parent_after.spent_amount == Decimal("1.5")
        assert parent_after.reserved_amount == Decimal("0")
        assert parent_after.available_amount == Decimal("8.5")
        assert store.get_receipt(reserved.lease.id) == record


def test_parent_receipt_waits_for_all_active_children(tmp_path) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root = _admission("root-admission", policy, _known_cost("1"))
    child = _admission("child-admission", policy, _known_cost("2"))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        (reserved,) = store.reserve_children(
            parent.lease.id,
            (ChildLeaseRequest("child-lease", child, T9),),
        )

        with pytest.raises(CostReceiptConflictError, match="child leases"):
            store.finalize_receipt(
                receipt_id="parent-receipt",
                lease_id=parent.lease.id,
                outcome=CostReceiptOutcome.SUCCEEDED,
                final_cost=_known_cost("0", observed_at=T2),
                reason_code="parent_completed",
            )

        store.finalize_receipt(
            receipt_id="child-receipt",
            lease_id=reserved.lease.id,
            outcome=CostReceiptOutcome.SUCCEEDED,
            final_cost=_known_cost("1", observed_at=T2),
            reason_code="child_completed",
        )
        parent_record = store.finalize_receipt(
            receipt_id="parent-receipt",
            lease_id=parent.lease.id,
            outcome=CostReceiptOutcome.SUCCEEDED,
            final_cost=_known_cost("1", observed_at=T2),
            reason_code="parent_completed",
        )

        assert parent_record.receipt.outcome is CostReceiptOutcome.SUCCEEDED
        assert store.get_balance(parent.lease.id).closed_at == T1


def test_concurrent_child_contention_never_exceeds_parent_headroom(tmp_path) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root = _admission("root-admission", policy, _known_cost("1"))
    admissions = tuple(
        _admission(f"child-admission-{index}", policy, _known_cost("1"))
        for index in range(20)
    )
    barrier = Barrier(len(admissions))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )

        def reserve(index: int) -> str:
            barrier.wait(timeout=5)
            try:
                store.reserve_children(
                    parent.lease.id,
                    (
                        ChildLeaseRequest(
                            f"child-lease-{index}",
                            admissions[index],
                            T9,
                        ),
                    ),
                )
            except BudgetHeadroomExceededError:
                return "denied"
            return "reserved"

        with ThreadPoolExecutor(max_workers=len(admissions)) as executor:
            results = tuple(executor.map(reserve, range(len(admissions))))

        parent_after = store.get_balance(parent.lease.id)
        children = store.list_children(parent.lease.id)

    assert results.count("reserved") == 10
    assert results.count("denied") == 10
    assert parent_after.reserved_amount == Decimal("10")
    assert parent_after.available_amount == Decimal("0")
    assert len(children) == 10
    assert sum(
        (item.lease.limit.amount or Decimal(0) for item in children),
        start=Decimal(0),
    ) == Decimal("10")


def test_finite_lease_transaction_p95_stays_within_five_milliseconds(
    tmp_path,
) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("1"))
    root = _admission("root-admission", policy, _known_cost("0"))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        store.record_cumulative_spend(
            parent.lease.id,
            _known_cost("0", observed_at=T2),
        )
        durations_ms: list[float] = []
        for index in range(1, 41):
            started = perf_counter()
            store.record_cumulative_spend(
                parent.lease.id,
                _known_cost(str(Decimal(index) / 100), observed_at=T2),
            )
            durations_ms.append((perf_counter() - started) * 1000)

    ordered = sorted(durations_ms)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    assert p95 <= 5.0


def test_receipt_rows_are_immutable_at_the_storage_boundary(tmp_path) -> None:
    current = [T1]
    policy = BudgetPolicy.finite("USD", Decimal("1"))
    root = _admission("root-admission", policy, _known_cost("0"))

    with _store(tmp_path, current) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        store.finalize_receipt(
            receipt_id="root-receipt",
            lease_id=parent.lease.id,
            outcome=CostReceiptOutcome.SUCCEEDED,
            final_cost=_known_cost("0", observed_at=T2),
            reason_code="parent_completed",
        )
        assert store.schema_version == 2
        database_path = store.path

    with sqlite3.connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE cost_receipts SET accounted_amount = '1'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM cost_receipts")


def _store(tmp_path, current: list[str]) -> BudgetLeaseStore:
    return BudgetLeaseStore(
        tmp_path,
        clock=lambda: datetime.fromisoformat(current[0].replace("Z", "+00:00")),
    )


def _admission(
    admission_id: str,
    policy: BudgetPolicy,
    cost: CostObservation,
) -> BudgetAdmission:
    admission = evaluate_budget_admission(
        admission_id=admission_id,
        policy=policy,
        requested_cost=cost,
        created_at=T0,
        price_source_digest=cost.source_digest,
    )
    assert admission.decision is BudgetAdmissionDecision.ADMITTED
    return admission


def _known_cost(
    amount: str,
    *,
    observed_at: str = T0,
) -> CostObservation:
    return CostObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class="priced_api",
        confidence=CostConfidence.ESTIMATED,
        source="price_catalog",
        source_digest=PRICE_DIGEST,
        observed_at=observed_at,
        currency="USD",
        amount=Decimal(amount),
        reason_code="catalog_estimate",
    )


def _unknown_cost(
    *,
    observed_at: str,
    route_class: str,
) -> CostObservation:
    return CostObservation(
        provider_id="openai",
        model_id="gpt-5.6",
        route_class=route_class,
        confidence=CostConfidence.UNKNOWN,
        source="provider_session",
        source_digest=PRICE_DIGEST,
        observed_at=observed_at,
        reason_code="monetary_cost_unavailable",
    )
