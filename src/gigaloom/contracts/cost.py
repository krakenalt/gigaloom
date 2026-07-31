"""Versioned, provider-neutral cost and budget contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re


COST_CONTRACT_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")


class BudgetPolicyKind(str, Enum):
    """Describe whether monetary admission is bounded or explicitly unlimited."""

    UNLIMITED = "unlimited"
    FINITE = "finite"


class CostConfidence(str, Enum):
    """Describe how strongly one cost or usage observation is supported."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class BudgetAdmissionDecision(str, Enum):
    """Describe the result of pre-spawn monetary admission."""

    ADMITTED = "admitted"
    DENIED = "denied"


class CostReceiptOutcome(str, Enum):
    """Describe why one admitted execution produced a terminal receipt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class BudgetPolicy:
    """Explicit unlimited or single-currency finite monetary policy."""

    kind: BudgetPolicyKind
    currency: str | None = None
    amount: Decimal | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="budget policy")
        if not isinstance(self.kind, BudgetPolicyKind):
            raise ValueError("budget policy kind is invalid")
        if self.kind is BudgetPolicyKind.UNLIMITED:
            if self.currency is not None or self.amount is not None:
                raise ValueError("unlimited budget policy cannot contain an amount")
            return
        _validate_currency(self.currency, field_name="budget policy currency")
        _validate_decimal(self.amount, field_name="budget policy amount")

    @classmethod
    def unlimited(cls) -> BudgetPolicy:
        """Create an explicit unlimited policy."""
        return cls(kind=BudgetPolicyKind.UNLIMITED)

    @classmethod
    def finite(cls, currency: str, amount: Decimal) -> BudgetPolicy:
        """Create a finite single-currency policy."""
        return cls(
            kind=BudgetPolicyKind.FINITE,
            currency=currency,
            amount=amount,
        )


@dataclass(frozen=True)
class CostObservation:
    """One monetary observation without credentials or billing identifiers."""

    provider_id: str
    model_id: str
    route_class: str
    confidence: CostConfidence
    source: str
    source_digest: str
    observed_at: str
    currency: str | None = None
    amount: Decimal | None = None
    reason_code: str | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="cost observation")
        _validate_observation_identity(self)
        _validate_known_or_unknown_amount(
            confidence=self.confidence,
            currency=self.currency,
            amount=self.amount,
            reason_code=self.reason_code,
            field_name="cost observation",
        )


@dataclass(frozen=True)
class TokenObservation:
    """Token usage kept separate from money and subscription quota."""

    provider_id: str
    model_id: str
    route_class: str
    confidence: CostConfidence
    source: str
    source_digest: str
    observed_at: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reason_code: str | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="token observation")
        _validate_observation_identity(self)
        counts = (self.input_tokens, self.output_tokens, self.total_tokens)
        for name, value in zip(
            ("input_tokens", "output_tokens", "total_tokens"),
            counts,
            strict=True,
        ):
            _validate_optional_non_negative_int(value, field_name=name)
        if self.confidence is CostConfidence.UNKNOWN:
            if any(value is not None for value in counts):
                raise ValueError("unknown token observation cannot contain counts")
            _validate_reason(self.reason_code, field_name="token observation")
            return
        if self.total_tokens is None:
            raise ValueError("known token observation requires total_tokens")
        if (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.input_tokens + self.output_tokens > self.total_tokens
        ):
            raise ValueError("token components cannot exceed total_tokens")
        _validate_optional_reason(self.reason_code, field_name="token observation")


@dataclass(frozen=True)
class SubscriptionQuotaObservation:
    """Subscription quota evidence that is never converted into money."""

    provider_id: str
    route_class: str
    quota_name: str
    unit: str
    confidence: CostConfidence
    source: str
    source_digest: str
    observed_at: str
    used: Decimal | None = None
    limit: Decimal | None = None
    remaining: Decimal | None = None
    reason_code: str | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="quota observation")
        _validate_observation_identity(self, include_model=False)
        _validate_identity(self.quota_name, field_name="quota name")
        _validate_identity(self.unit, field_name="quota unit")
        values = (self.used, self.limit, self.remaining)
        for name, value in zip(("used", "limit", "remaining"), values, strict=True):
            if value is not None:
                _validate_decimal(value, field_name=f"quota {name}")
        if self.confidence is CostConfidence.UNKNOWN:
            if any(value is not None for value in values):
                raise ValueError("unknown quota observation cannot contain values")
            _validate_reason(self.reason_code, field_name="quota observation")
            return
        if all(value is None for value in values):
            raise ValueError("known quota observation requires at least one value")
        _validate_optional_reason(self.reason_code, field_name="quota observation")


@dataclass(frozen=True)
class BudgetAdmission:
    """Immutable result of evaluating one route before execution starts."""

    id: str
    policy: BudgetPolicy
    decision: BudgetAdmissionDecision
    route_class: str
    requested_cost: CostObservation
    reason_code: str
    created_at: str
    price_source_digest: str | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="budget admission")
        _validate_identity(self.id, field_name="admission id")
        _validate_identity(self.route_class, field_name="admission route class")
        _validate_reason(self.reason_code, field_name="budget admission")
        _validate_timestamp(self.created_at, field_name="admission created_at")
        if not isinstance(self.policy, BudgetPolicy):
            raise ValueError("admission policy must be a BudgetPolicy")
        if not isinstance(self.decision, BudgetAdmissionDecision):
            raise ValueError("budget admission decision is invalid")
        if not isinstance(self.requested_cost, CostObservation):
            raise ValueError("admission requested_cost must be a CostObservation")
        if self.requested_cost.route_class != self.route_class:
            raise ValueError("admission route class does not match requested cost")
        if self.price_source_digest is not None:
            _validate_digest(
                self.price_source_digest,
                field_name="price_source_digest",
            )
        if (
            self.policy.kind is BudgetPolicyKind.FINITE
            and self.decision is BudgetAdmissionDecision.ADMITTED
        ):
            if self.requested_cost.confidence is CostConfidence.UNKNOWN:
                raise ValueError("finite policy cannot admit unknown monetary cost")
            if self.requested_cost.currency != self.policy.currency:
                raise ValueError("admitted cost currency does not match finite policy")
            if self.requested_cost.amount is None or self.policy.amount is None:
                raise ValueError("finite admission requires known monetary amounts")
            if self.requested_cost.amount > self.policy.amount:
                raise ValueError("admitted cost exceeds finite policy amount")
            if self.price_source_digest is None:
                raise ValueError("finite admission requires a price source digest")


@dataclass(frozen=True)
class BudgetLease:
    """One parent-bound child ceiling issued after budget admission."""

    id: str
    admission_id: str
    limit: BudgetPolicy
    issued_at: str
    expires_at: str
    parent_lease_id: str | None = None
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="budget lease")
        _validate_identity(self.id, field_name="lease id")
        _validate_identity(self.admission_id, field_name="lease admission id")
        if self.parent_lease_id is not None:
            _validate_identity(self.parent_lease_id, field_name="parent lease id")
            if self.parent_lease_id == self.id:
                raise ValueError("budget lease cannot be its own parent")
        if not isinstance(self.limit, BudgetPolicy):
            raise ValueError("lease limit must be a BudgetPolicy")
        issued = _validate_timestamp(self.issued_at, field_name="lease issued_at")
        expires = _validate_timestamp(self.expires_at, field_name="lease expires_at")
        if expires <= issued:
            raise ValueError("lease expires_at must be after issued_at")


@dataclass(frozen=True)
class CostReceipt:
    """Terminal cost evidence for success, failure, or cancellation."""

    id: str
    lease_id: str
    outcome: CostReceiptOutcome
    final_cost: CostObservation
    closed_at: str
    reason_code: str
    schema_version: int = COST_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema_version(self.schema_version, field_name="cost receipt")
        _validate_identity(self.id, field_name="receipt id")
        _validate_identity(self.lease_id, field_name="receipt lease id")
        if not isinstance(self.outcome, CostReceiptOutcome):
            raise ValueError("cost receipt outcome is invalid")
        if not isinstance(self.final_cost, CostObservation):
            raise ValueError("receipt final_cost must be a CostObservation")
        _validate_timestamp(self.closed_at, field_name="receipt closed_at")
        _validate_reason(self.reason_code, field_name="cost receipt")


def _validate_observation_identity(
    value: CostObservation | TokenObservation | SubscriptionQuotaObservation,
    *,
    include_model: bool = True,
) -> None:
    _validate_identity(value.provider_id, field_name="provider id")
    if include_model:
        model_id = getattr(value, "model_id", None)
        _validate_identity(model_id, field_name="model id")
    _validate_identity(value.route_class, field_name="route class")
    if not isinstance(value.confidence, CostConfidence):
        raise ValueError("cost confidence is invalid")
    _validate_identity(value.source, field_name="observation source")
    _validate_digest(value.source_digest, field_name="source_digest")
    _validate_timestamp(value.observed_at, field_name="observed_at")


def _validate_known_or_unknown_amount(
    *,
    confidence: CostConfidence,
    currency: str | None,
    amount: Decimal | None,
    reason_code: str | None,
    field_name: str,
) -> None:
    if confidence is CostConfidence.UNKNOWN:
        if currency is not None or amount is not None:
            raise ValueError(f"unknown {field_name} cannot contain an amount")
        _validate_reason(reason_code, field_name=field_name)
        return
    _validate_currency(currency, field_name=f"{field_name} currency")
    _validate_decimal(amount, field_name=f"{field_name} amount")
    _validate_optional_reason(reason_code, field_name=field_name)


def _validate_schema_version(value: int, *, field_name: str) -> None:
    if value != COST_CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported {field_name} schema_version")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_currency(value: str | None, *, field_name: str) -> None:
    if not isinstance(value, str) or _CURRENCY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a three-letter uppercase code")


def _validate_decimal(value: Decimal | None, *, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative Decimal")


def _validate_optional_non_negative_int(
    value: int | None,
    *,
    field_name: str,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_reason(value: str | None, *, field_name: str) -> None:
    if value is None:
        raise ValueError(f"{field_name} requires a reason_code")
    _validate_identity(value, field_name=f"{field_name} reason_code")


def _validate_optional_reason(value: str | None, *, field_name: str) -> None:
    if value is not None:
        _validate_identity(value, field_name=f"{field_name} reason_code")


def _validate_timestamp(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


__all__ = [
    "COST_CONTRACT_SCHEMA_VERSION",
    "BudgetAdmission",
    "BudgetAdmissionDecision",
    "BudgetLease",
    "BudgetPolicy",
    "BudgetPolicyKind",
    "CostConfidence",
    "CostObservation",
    "CostReceipt",
    "CostReceiptOutcome",
    "SubscriptionQuotaObservation",
    "TokenObservation",
]
