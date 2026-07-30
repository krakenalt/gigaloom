"""Runtime state and errors for finite budget lease enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from gigaloom.contracts.cost import (
    BudgetAdmission,
    BudgetLease,
    BudgetPolicyKind,
    CostReceipt,
)


class BudgetLeaseStatus(str, Enum):
    """Describe whether a lease can still accept reservations or spend."""

    ACTIVE = "active"
    CLOSED = "closed"


class CostBudgetError(RuntimeError):
    """Base error for deterministic budget admission and lease operations."""


class AdmissionDeniedError(CostBudgetError):
    """Raised when a denied admission is passed to lease storage."""


class BudgetLeaseNotFoundError(CostBudgetError):
    """Raised when a requested lease does not exist."""


class BudgetLeaseConflictError(CostBudgetError):
    """Raised when a lease id or immutable admission binding conflicts."""


class BudgetLeaseClosedError(CostBudgetError):
    """Raised when a closed lease is used for new work."""


class BudgetLeaseExpiredError(CostBudgetError):
    """Raised when a lease is no longer temporally valid."""


class BudgetHeadroomExceededError(CostBudgetError):
    """Raised before spawn or spend would exceed a finite ceiling."""


class CostObservationConflictError(CostBudgetError):
    """Raised when cumulative monetary evidence is inconsistent with a lease."""


class CostReceiptConflictError(CostBudgetError):
    """Raised when receipt finalization conflicts with durable lease evidence."""


@dataclass(frozen=True)
class ChildLeaseRequest:
    """One admitted child reservation in an atomic fan-out batch."""

    lease_id: str
    admission: BudgetAdmission
    expires_at: str


@dataclass(frozen=True)
class BudgetLeaseBalance:
    """Content-free runtime balance for one immutable lease binding."""

    lease: BudgetLease
    admission: BudgetAdmission
    status: BudgetLeaseStatus
    spent_amount: Decimal | None
    reserved_amount: Decimal | None
    version: int
    closed_at: str | None = None

    @property
    def available_amount(self) -> Decimal | None:
        """Return finite headroom or ``None`` for explicit unlimited policy."""
        if self.lease.limit.kind is BudgetPolicyKind.UNLIMITED:
            return None
        if (
            self.lease.limit.amount is None
            or self.spent_amount is None
            or self.reserved_amount is None
        ):
            raise ValueError("finite lease balance is incomplete")
        return self.lease.limit.amount - self.spent_amount - self.reserved_amount


@dataclass(frozen=True)
class CostReceiptRecord:
    """One immutable receipt plus conservative finite-budget accounting."""

    receipt: CostReceipt
    accounted_currency: str | None
    accounted_amount: Decimal | None


__all__ = [
    "AdmissionDeniedError",
    "BudgetHeadroomExceededError",
    "BudgetLeaseBalance",
    "BudgetLeaseClosedError",
    "BudgetLeaseConflictError",
    "BudgetLeaseExpiredError",
    "BudgetLeaseNotFoundError",
    "BudgetLeaseStatus",
    "ChildLeaseRequest",
    "CostBudgetError",
    "CostObservationConflictError",
    "CostReceiptConflictError",
    "CostReceiptRecord",
]
