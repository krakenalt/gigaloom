"""Atomic SQLite storage for finite parent-to-child budget leases."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Self

from gigaloom.contracts.cost import (
    BudgetAdmission,
    BudgetAdmissionDecision,
    BudgetLease,
    BudgetPolicy,
    BudgetPolicyKind,
    CostConfidence,
    CostObservation,
)
from gigaloom.contracts.cost_serialization import (
    budget_admission_from_dict,
    budget_admission_to_dict,
)
from gigaloom.runtime.cost.models import (
    AdmissionDeniedError,
    BudgetHeadroomExceededError,
    BudgetLeaseBalance,
    BudgetLeaseClosedError,
    BudgetLeaseConflictError,
    BudgetLeaseExpiredError,
    BudgetLeaseNotFoundError,
    BudgetLeaseStatus,
    ChildLeaseRequest,
    CostObservationConflictError,
)
from gigaloom.runtime.db import DbProvider, transaction


COST_DB_NAME = "cost.sqlite3"
COST_DB_SCHEMA_VERSION = 1


class BudgetLeaseStore:
    """Own atomic monetary headroom for one GigaLoom data root."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        filename: str = COST_DB_NAME,
        timeout_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.path = self.data_dir / filename
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db = DbProvider(self.path, timeout_seconds=timeout_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._migrate()

    def __enter__(self) -> Self:
        """Return this store as a managed resource."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close operation-scoped database resources."""
        self.close()

    def close(self) -> None:
        """Reject future database connections."""
        self._db.close()

    @property
    def schema_version(self) -> int:
        """Return the exact cost database schema version."""
        with self._db.connect() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def open_parent_lease(
        self,
        *,
        lease_id: str,
        admission: BudgetAdmission,
        expires_at: str,
    ) -> BudgetLeaseBalance:
        """Open an idempotent root lease from one admitted budget policy."""
        _require_admitted(admission)
        with self._db.connect() as connection, transaction(connection):
            existing = self._optional_balance(connection, lease_id)
            if existing is not None:
                if (
                    existing.admission == admission
                    and existing.lease.parent_lease_id is None
                    and existing.lease.limit == admission.policy
                    and existing.lease.expires_at == expires_at
                ):
                    return existing
                raise BudgetLeaseConflictError(
                    f"budget lease {lease_id} has a conflicting immutable binding"
                )
            lease = BudgetLease(
                id=lease_id,
                admission_id=admission.id,
                limit=admission.policy,
                issued_at=_timestamp_text(self._now()),
                expires_at=expires_at,
            )
            try:
                self._insert_lease(connection, lease=lease, admission=admission)
            except sqlite3.IntegrityError as exc:
                raise BudgetLeaseConflictError(
                    "parent lease or admission identity already exists"
                ) from exc
            return self._balance(connection, lease_id)

    def reserve_children(
        self,
        parent_lease_id: str,
        requests: Iterable[ChildLeaseRequest],
    ) -> tuple[BudgetLeaseBalance, ...]:
        """Atomically reserve all child ceilings or none of them."""
        batch = tuple(requests)
        if not batch:
            raise ValueError("child lease reservation batch cannot be empty")
        lease_ids = [item.lease_id for item in batch]
        admission_ids = [item.admission.id for item in batch]
        if len(set(lease_ids)) != len(lease_ids):
            raise BudgetLeaseConflictError("child lease ids must be unique")
        if len(set(admission_ids)) != len(admission_ids):
            raise BudgetLeaseConflictError("child admission ids must be unique")

        with self._db.connect() as connection, transaction(connection):
            parent = self._balance(connection, parent_lease_id)
            self._require_active(parent)
            issued_at = _timestamp_text(self._now())
            leases = tuple(
                self._child_lease(parent, request, issued_at=issued_at)
                for request in batch
            )
            existing = tuple(
                self._optional_balance(connection, lease.id) for lease in leases
            )
            if all(item is not None for item in existing):
                balances = tuple(item for item in existing if item is not None)
                if all(
                    _same_lease_request(balance.lease, lease)
                    and balance.admission == request.admission
                    for balance, lease, request in zip(
                        balances,
                        leases,
                        batch,
                        strict=True,
                    )
                ):
                    return balances
                raise BudgetLeaseConflictError(
                    "child lease batch conflicts with existing bindings"
                )
            if any(item is not None for item in existing):
                raise BudgetLeaseConflictError(
                    "child lease batch is only partially present"
                )
            _require_not_expired(parent.lease, issued_at)

            reservation = sum(
                (
                    lease.limit.amount
                    for lease in leases
                    if lease.limit.amount is not None
                ),
                start=Decimal(0),
            )
            if parent.lease.limit.kind is BudgetPolicyKind.FINITE:
                available = parent.available_amount
                if available is None or reservation > available:
                    raise BudgetHeadroomExceededError(
                        f"child reservation exceeds lease {parent_lease_id} headroom"
                    )
                connection.execute(
                    """
                    UPDATE cost_budget_leases
                    SET reserved_amount = ?, version = version + 1
                    WHERE id = ? AND version = ?
                    """,
                    (
                        _decimal_text(
                            (parent.reserved_amount or Decimal(0)) + reservation
                        ),
                        parent_lease_id,
                        parent.version,
                    ),
                )

            try:
                for lease, request in zip(leases, batch, strict=True):
                    self._insert_lease(
                        connection,
                        lease=lease,
                        admission=request.admission,
                    )
            except sqlite3.IntegrityError as exc:
                raise BudgetLeaseConflictError(
                    "child lease or admission identity already exists"
                ) from exc
            return tuple(self._balance(connection, lease.id) for lease in leases)

    def record_cumulative_spend(
        self,
        lease_id: str,
        observation: CostObservation,
    ) -> BudgetLeaseBalance:
        """Enforce a monotonic cumulative observation against finite headroom."""
        with self._db.connect() as connection, transaction(connection):
            balance = self._balance(connection, lease_id)
            self._require_active(balance)
            _require_observation_route(balance.admission, observation)
            _require_not_expired(balance.lease, _timestamp_text(self._now()))
            if balance.lease.limit.kind is BudgetPolicyKind.UNLIMITED:
                return balance
            if observation.confidence is CostConfidence.UNKNOWN:
                raise CostObservationConflictError(
                    "finite lease cannot record unknown cumulative spend"
                )
            if observation.currency != balance.lease.limit.currency:
                raise CostObservationConflictError(
                    "spend currency does not match finite lease"
                )
            if observation.amount is None:
                raise CostObservationConflictError(
                    "finite lease spend requires a monetary amount"
                )
            current = balance.spent_amount or Decimal(0)
            if observation.amount < current:
                raise CostObservationConflictError(
                    "cumulative spend cannot move backwards"
                )
            reserved = balance.reserved_amount or Decimal(0)
            limit = balance.lease.limit.amount
            if limit is None or observation.amount + reserved > limit:
                raise BudgetHeadroomExceededError(
                    f"spend exceeds lease {lease_id} headroom"
                )
            connection.execute(
                """
                UPDATE cost_budget_leases
                SET spent_amount = ?, version = version + 1
                WHERE id = ? AND version = ?
                """,
                (_decimal_text(observation.amount), lease_id, balance.version),
            )
            return self._balance(connection, lease_id)

    def get_balance(self, lease_id: str) -> BudgetLeaseBalance:
        """Return one content-free lease balance."""
        with self._db.connect() as connection:
            return self._balance(connection, lease_id)

    def list_children(self, parent_lease_id: str) -> tuple[BudgetLeaseBalance, ...]:
        """List child leases in stable issuance order."""
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM cost_budget_leases
                WHERE parent_lease_id = ?
                ORDER BY issued_at, id
                """,
                (parent_lease_id,),
            ).fetchall()
        return tuple(_balance_from_row(row) for row in rows)

    def _migrate(self) -> None:
        with self._db.connect() as connection, transaction(connection):
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current == COST_DB_SCHEMA_VERSION:
                return
            if current != 0:
                raise RuntimeError(
                    f"unsupported cost database schema version {current}"
                )
            connection.execute(
                """
                CREATE TABLE cost_budget_leases (
                    id TEXT PRIMARY KEY,
                    admission_id TEXT NOT NULL UNIQUE,
                    admission_json TEXT NOT NULL,
                    parent_lease_id TEXT
                        REFERENCES cost_budget_leases(id) ON DELETE RESTRICT,
                    limit_kind TEXT NOT NULL
                        CHECK (limit_kind IN ('unlimited', 'finite')),
                    currency TEXT,
                    limit_amount TEXT,
                    spent_amount TEXT,
                    reserved_amount TEXT,
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'closed')),
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    closed_at TEXT,
                    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                    CHECK (
                        (
                            limit_kind = 'unlimited'
                            AND currency IS NULL
                            AND limit_amount IS NULL
                            AND spent_amount IS NULL
                            AND reserved_amount IS NULL
                        )
                        OR
                        (
                            limit_kind = 'finite'
                            AND currency IS NOT NULL
                            AND limit_amount IS NOT NULL
                            AND spent_amount IS NOT NULL
                            AND reserved_amount IS NOT NULL
                        )
                    )
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX cost_budget_leases_parent_idx
                ON cost_budget_leases(parent_lease_id, status, issued_at)
                """
            )
            connection.execute(f"PRAGMA user_version = {COST_DB_SCHEMA_VERSION}")

    def _insert_lease(
        self,
        connection: sqlite3.Connection,
        *,
        lease: BudgetLease,
        admission: BudgetAdmission,
    ) -> None:
        finite = lease.limit.kind is BudgetPolicyKind.FINITE
        connection.execute(
            """
            INSERT INTO cost_budget_leases (
                id, admission_id, admission_json, parent_lease_id,
                limit_kind, currency, limit_amount,
                spent_amount, reserved_amount, status,
                issued_at, expires_at, closed_at, version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, 0)
            """,
            (
                lease.id,
                lease.admission_id,
                _admission_json(admission),
                lease.parent_lease_id,
                lease.limit.kind.value,
                lease.limit.currency,
                _optional_decimal_text(lease.limit.amount),
                "0" if finite else None,
                "0" if finite else None,
                lease.issued_at,
                lease.expires_at,
            ),
        )

    def _child_lease(
        self,
        parent: BudgetLeaseBalance,
        request: ChildLeaseRequest,
        *,
        issued_at: str,
    ) -> BudgetLease:
        _require_admitted(request.admission)
        if request.admission.policy != parent.admission.policy:
            raise BudgetLeaseConflictError(
                "child admission policy does not match parent policy"
            )
        if _parse_timestamp(request.expires_at) > _parse_timestamp(
            parent.lease.expires_at
        ):
            raise BudgetLeaseExpiredError("child lease cannot outlive its parent")
        if parent.lease.limit.kind is BudgetPolicyKind.UNLIMITED:
            limit = BudgetPolicy.unlimited()
        else:
            requested = request.admission.requested_cost
            if (
                requested.confidence is CostConfidence.UNKNOWN
                or requested.currency != parent.lease.limit.currency
                or requested.amount is None
            ):
                raise AdmissionDeniedError(
                    "finite child reservation requires admitted known cost"
                )
            limit = BudgetPolicy.finite(requested.currency, requested.amount)
        return BudgetLease(
            id=request.lease_id,
            admission_id=request.admission.id,
            parent_lease_id=parent.lease.id,
            limit=limit,
            issued_at=issued_at,
            expires_at=request.expires_at,
        )

    def _optional_balance(
        self,
        connection: sqlite3.Connection,
        lease_id: str,
    ) -> BudgetLeaseBalance | None:
        row = connection.execute(
            "SELECT * FROM cost_budget_leases WHERE id = ?",
            (lease_id,),
        ).fetchone()
        return _balance_from_row(row) if row is not None else None

    def _balance(
        self,
        connection: sqlite3.Connection,
        lease_id: str,
    ) -> BudgetLeaseBalance:
        balance = self._optional_balance(connection, lease_id)
        if balance is None:
            raise BudgetLeaseNotFoundError(f"budget lease {lease_id} was not found")
        return balance

    @staticmethod
    def _require_active(balance: BudgetLeaseBalance) -> None:
        if balance.status is BudgetLeaseStatus.CLOSED:
            raise BudgetLeaseClosedError(f"budget lease {balance.lease.id} is closed")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("budget lease clock must return a timezone-aware datetime")
        return value


def _balance_from_row(row: sqlite3.Row) -> BudgetLeaseBalance:
    admission_payload = json.loads(str(row["admission_json"]))
    if not isinstance(admission_payload, dict):
        raise ValueError("stored budget admission must be an object")
    admission = budget_admission_from_dict(admission_payload)
    kind = BudgetPolicyKind(str(row["limit_kind"]))
    limit = (
        BudgetPolicy.unlimited()
        if kind is BudgetPolicyKind.UNLIMITED
        else BudgetPolicy.finite(
            str(row["currency"]),
            Decimal(str(row["limit_amount"])),
        )
    )
    lease = BudgetLease(
        id=str(row["id"]),
        admission_id=str(row["admission_id"]),
        parent_lease_id=_optional_text(row["parent_lease_id"]),
        limit=limit,
        issued_at=str(row["issued_at"]),
        expires_at=str(row["expires_at"]),
    )
    return BudgetLeaseBalance(
        lease=lease,
        admission=admission,
        status=BudgetLeaseStatus(str(row["status"])),
        spent_amount=_optional_decimal(row["spent_amount"]),
        reserved_amount=_optional_decimal(row["reserved_amount"]),
        version=int(row["version"]),
        closed_at=_optional_text(row["closed_at"]),
    )


def _require_admitted(admission: BudgetAdmission) -> None:
    if admission.decision is not BudgetAdmissionDecision.ADMITTED:
        raise AdmissionDeniedError(
            f"budget admission {admission.id} was denied: {admission.reason_code}"
        )


def _require_observation_route(
    admission: BudgetAdmission,
    observation: CostObservation,
) -> None:
    if observation.route_class != admission.route_class:
        raise CostObservationConflictError(
            "cost observation route does not match lease admission"
        )
    if observation.provider_id != admission.requested_cost.provider_id:
        raise CostObservationConflictError(
            "cost observation provider does not match lease admission"
        )
    if observation.model_id != admission.requested_cost.model_id:
        raise CostObservationConflictError(
            "cost observation model does not match lease admission"
        )


def _require_not_expired(lease: BudgetLease, at: str) -> None:
    if _parse_timestamp(at) >= _parse_timestamp(lease.expires_at):
        raise BudgetLeaseExpiredError(f"budget lease {lease.id} is expired")
    if _parse_timestamp(at) < _parse_timestamp(lease.issued_at):
        raise BudgetLeaseExpiredError(f"budget lease {lease.id} is not active yet")


def _same_lease_request(existing: BudgetLease, requested: BudgetLease) -> bool:
    return (
        existing.id == requested.id
        and existing.admission_id == requested.admission_id
        and existing.parent_lease_id == requested.parent_lease_id
        and existing.limit == requested.limit
        and existing.expires_at == requested.expires_at
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("budget timestamps must include a timezone")
    return parsed


def _timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _admission_json(admission: BudgetAdmission) -> str:
    return json.dumps(
        budget_admission_to_dict(admission),
        sort_keys=True,
        separators=(",", ":"),
    )


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return _decimal_text(value) if value is not None else None


def _optional_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "COST_DB_NAME",
    "COST_DB_SCHEMA_VERSION",
    "BudgetLeaseStore",
]
