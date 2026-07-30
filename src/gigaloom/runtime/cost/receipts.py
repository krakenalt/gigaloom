"""Atomic terminal receipt finalization for budget leases."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import sqlite3
from typing import Callable

from gigaloom.contracts.cost import (
    BudgetPolicyKind,
    CostConfidence,
    CostObservation,
    CostReceipt,
    CostReceiptOutcome,
)
from gigaloom.contracts.cost_serialization import (
    budget_admission_from_dict,
    cost_receipt_from_dict,
    cost_receipt_to_dict,
)
from gigaloom.runtime.cost.models import (
    BudgetHeadroomExceededError,
    BudgetLeaseNotFoundError,
    CostObservationConflictError,
    CostReceiptConflictError,
    CostReceiptRecord,
)
from gigaloom.runtime.db import DbProvider, transaction


class CostReceiptRepository:
    """Close leases and return unused child headroom exactly once."""

    def __init__(
        self,
        db: DbProvider,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._db = db
        self._clock = clock

    def finalize(
        self,
        *,
        receipt_id: str,
        lease_id: str,
        outcome: CostReceiptOutcome,
        final_cost: CostObservation,
        reason_code: str,
    ) -> CostReceiptRecord:
        """Persist one idempotent terminal receipt and close its lease."""
        with self._db.connect() as connection, transaction(connection):
            lease = connection.execute(
                "SELECT * FROM cost_budget_leases WHERE id = ?",
                (lease_id,),
            ).fetchone()
            if lease is None:
                raise BudgetLeaseNotFoundError(f"budget lease {lease_id} was not found")
            existing = self._optional_for_lease(connection, lease_id)
            if existing is not None:
                if _same_receipt_request(
                    existing.receipt,
                    receipt_id=receipt_id,
                    outcome=outcome,
                    final_cost=final_cost,
                    reason_code=reason_code,
                ):
                    return existing
                raise CostReceiptConflictError(
                    f"budget lease {lease_id} already has a different receipt"
                )
            if str(lease["status"]) != "active":
                raise CostReceiptConflictError(
                    f"budget lease {lease_id} is closed without matching receipt"
                )
            active_child = connection.execute(
                """
                SELECT 1
                FROM cost_budget_leases
                WHERE parent_lease_id = ? AND status = 'active'
                LIMIT 1
                """,
                (lease_id,),
            ).fetchone()
            if active_child is not None:
                raise CostReceiptConflictError(
                    "parent lease cannot close while child leases remain active"
                )

            admission_payload = json.loads(str(lease["admission_json"]))
            if not isinstance(admission_payload, dict):
                raise CostReceiptConflictError("stored lease admission is malformed")
            admission = budget_admission_from_dict(admission_payload)
            _require_matching_observation(admission.requested_cost, final_cost)
            accounted_currency, accounted_amount = _accounted_final_cost(
                lease,
                final_cost,
            )
            closed_at = _timestamp_text(self._clock())
            receipt = CostReceipt(
                id=receipt_id,
                lease_id=lease_id,
                outcome=outcome,
                final_cost=final_cost,
                closed_at=closed_at,
                reason_code=reason_code,
            )

            self._charge_parent(
                connection,
                lease,
                accounted_amount=accounted_amount,
            )
            stored_spend = (
                accounted_amount
                if final_cost.confidence is not CostConfidence.UNKNOWN
                else _optional_decimal(lease["spent_amount"])
            )
            connection.execute(
                """
                UPDATE cost_budget_leases
                SET status = 'closed', spent_amount = ?, closed_at = ?,
                    version = version + 1
                WHERE id = ? AND status = 'active'
                """,
                (
                    _optional_decimal_text(stored_spend),
                    closed_at,
                    lease_id,
                ),
            )
            try:
                connection.execute(
                    """
                    INSERT INTO cost_receipts (
                        id, lease_id, receipt_json,
                        accounted_currency, accounted_amount, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.id,
                        receipt.lease_id,
                        _receipt_json(receipt),
                        accounted_currency,
                        _optional_decimal_text(accounted_amount),
                        closed_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise CostReceiptConflictError(
                    "receipt identity already exists"
                ) from exc
            return CostReceiptRecord(
                receipt=receipt,
                accounted_currency=accounted_currency,
                accounted_amount=accounted_amount,
            )

    def get_for_lease(self, lease_id: str) -> CostReceiptRecord:
        """Return the immutable terminal receipt for one lease."""
        with self._db.connect() as connection:
            record = self._optional_for_lease(connection, lease_id)
        if record is None:
            raise BudgetLeaseNotFoundError(
                f"cost receipt for lease {lease_id} was not found"
            )
        return record

    def _optional_for_lease(
        self,
        connection: sqlite3.Connection,
        lease_id: str,
    ) -> CostReceiptRecord | None:
        row = connection.execute(
            "SELECT * FROM cost_receipts WHERE lease_id = ?",
            (lease_id,),
        ).fetchone()
        return _receipt_record_from_row(row) if row is not None else None

    @staticmethod
    def _charge_parent(
        connection: sqlite3.Connection,
        lease: sqlite3.Row,
        *,
        accounted_amount: Decimal | None,
    ) -> None:
        parent_id = lease["parent_lease_id"]
        if parent_id is None:
            return
        parent = connection.execute(
            "SELECT * FROM cost_budget_leases WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if parent is None:
            raise CostReceiptConflictError("parent lease is missing")
        if str(parent["limit_kind"]) == BudgetPolicyKind.UNLIMITED.value:
            return
        child_limit = _required_decimal(lease["limit_amount"], "child limit")
        parent_reserved = _required_decimal(
            parent["reserved_amount"],
            "parent reserved amount",
        )
        parent_spent = _required_decimal(
            parent["spent_amount"],
            "parent spent amount",
        )
        parent_limit = _required_decimal(parent["limit_amount"], "parent limit")
        if parent_reserved < child_limit:
            raise CostReceiptConflictError(
                "parent reservation is smaller than child limit"
            )
        charged = accounted_amount if accounted_amount is not None else Decimal(0)
        new_reserved = parent_reserved - child_limit
        new_spent = parent_spent + charged
        if new_spent + new_reserved > parent_limit:
            raise BudgetHeadroomExceededError(
                "receipt accounting exceeds parent lease headroom"
            )
        connection.execute(
            """
            UPDATE cost_budget_leases
            SET reserved_amount = ?, spent_amount = ?, version = version + 1
            WHERE id = ?
            """,
            (
                _decimal_text(new_reserved),
                _decimal_text(new_spent),
                str(parent_id),
            ),
        )


def _accounted_final_cost(
    lease: sqlite3.Row,
    final_cost: CostObservation,
) -> tuple[str | None, Decimal | None]:
    if str(lease["limit_kind"]) == BudgetPolicyKind.UNLIMITED.value:
        return final_cost.currency, final_cost.amount
    currency = str(lease["currency"])
    limit = _required_decimal(lease["limit_amount"], "lease limit")
    observed = _required_decimal(lease["spent_amount"], "lease spent amount")
    if final_cost.confidence is CostConfidence.UNKNOWN:
        return currency, limit
    if final_cost.currency != currency or final_cost.amount is None:
        raise CostObservationConflictError(
            "final cost does not match finite lease currency"
        )
    if final_cost.amount < observed:
        raise CostObservationConflictError(
            "final cost cannot be below cumulative spend"
        )
    if final_cost.amount > limit:
        raise BudgetHeadroomExceededError("final cost exceeds finite lease")
    return currency, final_cost.amount


def _require_matching_observation(
    expected: CostObservation,
    actual: CostObservation,
) -> None:
    if (
        actual.provider_id != expected.provider_id
        or actual.model_id != expected.model_id
        or actual.route_class != expected.route_class
    ):
        raise CostObservationConflictError(
            "final cost route does not match lease admission"
        )


def _same_receipt_request(
    existing: CostReceipt,
    *,
    receipt_id: str,
    outcome: CostReceiptOutcome,
    final_cost: CostObservation,
    reason_code: str,
) -> bool:
    return (
        existing.id == receipt_id
        and existing.outcome is outcome
        and existing.final_cost == final_cost
        and existing.reason_code == reason_code
    )


def _receipt_record_from_row(row: sqlite3.Row) -> CostReceiptRecord:
    payload = json.loads(str(row["receipt_json"]))
    if not isinstance(payload, dict):
        raise CostReceiptConflictError("stored cost receipt is malformed")
    return CostReceiptRecord(
        receipt=cost_receipt_from_dict(payload),
        accounted_currency=_optional_text(row["accounted_currency"]),
        accounted_amount=_optional_decimal(row["accounted_amount"]),
    )


def _receipt_json(receipt: CostReceipt) -> str:
    return json.dumps(
        cost_receipt_to_dict(receipt),
        sort_keys=True,
        separators=(",", ":"),
    )


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cost receipt clock must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


def _required_decimal(value: object, field_name: str) -> Decimal:
    if value is None:
        raise CostReceiptConflictError(f"{field_name} is missing")
    return Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


__all__ = ["CostReceiptRepository"]
