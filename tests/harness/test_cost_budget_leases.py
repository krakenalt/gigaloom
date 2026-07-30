"""Tests for pre-spawn admission and finite parent-to-child leases."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from gigaloom.contracts import (
    BudgetAdmissionDecision,
    BudgetPolicy,
    CostConfidence,
    CostObservation,
)
from gigaloom.runtime.api import (
    AdmissionDeniedError,
    BudgetHeadroomExceededError,
    BudgetLeaseConflictError,
    BudgetLeaseStore,
    ChildLeaseRequest,
    CostObservationConflictError,
    evaluate_budget_admission,
)


T0 = "2026-07-30T10:00:00Z"
T1 = "2026-07-30T10:01:00Z"
T2 = "2026-07-30T10:02:00Z"
T8 = "2026-07-30T10:08:00Z"
T9 = "2026-07-30T10:09:00Z"
T10 = "2026-07-30T10:10:00Z"
PRICE_DIGEST = "a" * 64


@pytest.mark.parametrize(
    (
        "cost_kind",
        "amount",
        "currency",
        "price_digest",
        "expected_decision",
        "expected_reason",
    ),
    [
        (
            "known",
            "2.50",
            "USD",
            PRICE_DIGEST,
            BudgetAdmissionDecision.ADMITTED,
            "within_policy",
        ),
        (
            "unknown",
            None,
            None,
            None,
            BudgetAdmissionDecision.DENIED,
            "unknown_monetary_cost",
        ),
        (
            "known",
            "2.50",
            "USD",
            None,
            BudgetAdmissionDecision.DENIED,
            "price_source_missing",
        ),
        (
            "known",
            "2.50",
            "USD",
            "b" * 64,
            BudgetAdmissionDecision.DENIED,
            "price_source_mismatch",
        ),
        (
            "known",
            "2.50",
            "EUR",
            PRICE_DIGEST,
            BudgetAdmissionDecision.DENIED,
            "currency_mismatch",
        ),
        (
            "known",
            "10.01",
            "USD",
            PRICE_DIGEST,
            BudgetAdmissionDecision.DENIED,
            "request_exceeds_policy",
        ),
    ],
)
def test_finite_pre_spawn_admission_is_deterministic_and_fail_closed(
    cost_kind: str,
    amount: str | None,
    currency: str | None,
    price_digest: str | None,
    expected_decision: BudgetAdmissionDecision,
    expected_reason: str,
) -> None:
    cost = (
        _unknown_cost()
        if cost_kind == "unknown"
        else replace(_known_cost(amount or "0"), currency=currency)
    )
    admission = evaluate_budget_admission(
        admission_id="admission-case",
        policy=BudgetPolicy.finite("USD", Decimal("10")),
        requested_cost=cost,
        created_at=T0,
        price_source_digest=price_digest,
    )

    assert admission.decision is expected_decision
    assert admission.reason_code == expected_reason
    assert admission.price_source_digest == (
        PRICE_DIGEST if expected_decision is BudgetAdmissionDecision.ADMITTED else None
    )


def test_unlimited_admission_is_explicit_without_inventing_monetary_cost() -> None:
    admission = evaluate_budget_admission(
        admission_id="admission-unlimited",
        policy=BudgetPolicy.unlimited(),
        requested_cost=_unknown_cost(),
        created_at=T0,
    )

    assert admission.decision is BudgetAdmissionDecision.ADMITTED
    assert admission.reason_code == "explicit_unlimited_policy"
    assert admission.requested_cost.amount is None
    assert admission.price_source_digest is None


def test_atomic_child_batch_reserves_parent_headroom_before_spawn(tmp_path) -> None:
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root_admission = _admission("root-admission", policy, _known_cost("1"))
    first = _admission("child-admission-1", policy, _known_cost("3"))
    second = _admission("child-admission-2", policy, _known_cost("4"))

    with _store(tmp_path) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root_admission,
            expires_at=T10,
        )
        requests = (
            ChildLeaseRequest("child-lease-1", first, T9),
            ChildLeaseRequest("child-lease-2", second, T9),
        )

        children = store.reserve_children(parent.lease.id, requests)
        retried = store.reserve_children(parent.lease.id, requests)
        refreshed = store.get_balance(parent.lease.id)

        assert tuple(item.lease for item in retried) == tuple(
            item.lease for item in children
        )
        assert [item.lease.limit.amount for item in children] == [
            Decimal("3"),
            Decimal("4"),
        ]
        assert refreshed.reserved_amount == Decimal("7")
        assert refreshed.available_amount == Decimal("3")
        assert refreshed.version == 1
        assert len(store.list_children(parent.lease.id)) == 2

        denied = _admission(
            "child-admission-3",
            policy,
            _known_cost("3.01"),
        )
        with pytest.raises(BudgetHeadroomExceededError, match="headroom"):
            store.reserve_children(
                parent.lease.id,
                (ChildLeaseRequest("child-lease-3", denied, T9),),
            )
        assert len(store.list_children(parent.lease.id)) == 2
        assert store.get_balance(parent.lease.id).reserved_amount == Decimal("7")


def test_finite_spend_is_monotonic_and_cannot_consume_reserved_headroom(
    tmp_path,
) -> None:
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root_admission = _admission("root-admission", policy, _known_cost("1"))
    child_admission = _admission(
        "child-admission",
        policy,
        _known_cost("3"),
    )

    with _store(tmp_path) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root_admission,
            expires_at=T10,
        )
        (child,) = store.reserve_children(
            parent.lease.id,
            (ChildLeaseRequest("child-lease", child_admission, T9),),
        )

        first = store.record_cumulative_spend(
            child.lease.id,
            _known_cost("1", observed_at=T2),
        )
        second = store.record_cumulative_spend(
            child.lease.id,
            _known_cost("2", observed_at=T8),
        )

        assert first.spent_amount == Decimal("1")
        assert second.spent_amount == Decimal("2")
        assert second.available_amount == Decimal("1")

        with pytest.raises(CostObservationConflictError, match="backwards"):
            store.record_cumulative_spend(
                child.lease.id,
                _known_cost("1.99", observed_at=T8),
            )
        with pytest.raises(BudgetHeadroomExceededError, match="headroom"):
            store.record_cumulative_spend(
                child.lease.id,
                _known_cost("3.01", observed_at=T8),
            )
        with pytest.raises(CostObservationConflictError, match="unknown"):
            store.record_cumulative_spend(
                child.lease.id,
                _unknown_cost(observed_at=T8, route_class="priced_api"),
            )

        store.record_cumulative_spend(
            parent.lease.id,
            _known_cost("7", observed_at=T8),
        )
        with pytest.raises(BudgetHeadroomExceededError, match="headroom"):
            store.record_cumulative_spend(
                parent.lease.id,
                _known_cost("7.01", observed_at=T8),
            )


def test_denied_and_conflicting_admissions_never_create_a_lease(tmp_path) -> None:
    denied = evaluate_budget_admission(
        admission_id="denied-admission",
        policy=BudgetPolicy.finite("USD", Decimal("10")),
        requested_cost=_unknown_cost(),
        created_at=T0,
    )
    admitted = _admission(
        "admitted",
        BudgetPolicy.finite("USD", Decimal("10")),
        _known_cost("1"),
    )

    with _store(tmp_path) as store:
        with pytest.raises(AdmissionDeniedError, match="unknown_monetary_cost"):
            store.open_parent_lease(
                lease_id="denied-lease",
                admission=denied,
                expires_at=T10,
            )

        created = store.open_parent_lease(
            lease_id="parent-lease",
            admission=admitted,
            expires_at=T10,
        )
        assert (
            store.open_parent_lease(
                lease_id="parent-lease",
                admission=admitted,
                expires_at=T10,
            )
            == created
        )

        with pytest.raises(BudgetLeaseConflictError, match="conflicting"):
            store.open_parent_lease(
                lease_id="parent-lease",
                admission=replace(admitted, id="other-admission"),
                expires_at=T10,
            )


def test_idempotent_retries_preserve_server_issued_timestamps(tmp_path) -> None:
    current = [datetime.fromisoformat(T1.replace("Z", "+00:00"))]
    policy = BudgetPolicy.finite("USD", Decimal("10"))
    root = _admission("root-admission", policy, _known_cost("1"))
    child = _admission("child-admission", policy, _known_cost("2"))

    with BudgetLeaseStore(tmp_path, clock=lambda: current[0]) as store:
        parent = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        current[0] = datetime.fromisoformat(T2.replace("Z", "+00:00"))
        parent_retry = store.open_parent_lease(
            lease_id="parent-lease",
            admission=root,
            expires_at=T10,
        )
        request = ChildLeaseRequest("child-lease", child, T9)
        (reserved,) = store.reserve_children(parent.lease.id, (request,))

        current[0] = datetime.fromisoformat(T8.replace("Z", "+00:00"))
        (reservation_retry,) = store.reserve_children(parent.lease.id, (request,))

    assert parent_retry.lease.issued_at == T1
    assert reservation_retry.lease.issued_at == reserved.lease.issued_at == T2


def test_cost_storage_is_isolated_from_general_runtime_database(tmp_path) -> None:
    with _store(tmp_path) as store:
        assert store.schema_version == 2
        assert store.path == tmp_path / "cost.sqlite3"

    assert (tmp_path / "cost.sqlite3").is_file()
    assert not (tmp_path / "runtime.sqlite3").exists()


def _store(tmp_path) -> BudgetLeaseStore:
    now = datetime.fromisoformat(T1.replace("Z", "+00:00"))
    return BudgetLeaseStore(tmp_path, clock=lambda: now)


def _admission(
    admission_id: str,
    policy: BudgetPolicy,
    cost: CostObservation,
):
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
    observed_at: str = T0,
    route_class: str = "native_subscription",
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
