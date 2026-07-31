"""Immutable route decision receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import re


ROUTE_DECISION_SCHEMA_VERSION = 1
MAX_ROUTE_DECISION_BYTES = 1_048_576
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_DECISION_ID_RE = re.compile(r"route_[a-z0-9][a-z0-9_-]{0,127}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")


class RouteDecisionError(ValueError):
    """Base error for immutable route decision receipts."""


class RouteDecisionConflictError(RouteDecisionError):
    """Raised when a stored receipt id has different immutable content."""


class RouteDecisionNotFoundError(RouteDecisionError):
    """Raised when a requested receipt does not exist."""


class RouteDecisionVerificationError(RouteDecisionError):
    """Raised when receipt integrity or current bindings do not verify."""


class RouteDecisionOutcome(str, Enum):
    """Persisted recommendation outcome without execution authority."""

    RECOMMENDED = "recommended"
    NEEDS_HUMAN = "needs_human"
    NO_ELIGIBLE_ROUTE = "no_eligible_route"


class RouteDecisionCostKnowledge(str, Enum):
    """Honest persisted monetary knowledge."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RouteDecisionCostEvidenceV1:
    """Receipt-owned monetary projection without invented zeroes."""

    knowledge: RouteDecisionCostKnowledge
    currency: str | None
    amount: Decimal | None
    headroom: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge, RouteDecisionCostKnowledge):
            raise RouteDecisionError("route decision cost knowledge is invalid")
        if self.knowledge is RouteDecisionCostKnowledge.UNKNOWN:
            if any(
                value is not None
                for value in (self.currency, self.amount, self.headroom)
            ):
                raise RouteDecisionError(
                    "unknown route decision cost cannot contain monetary values"
                )
            return
        if (
            not isinstance(self.currency, str)
            or _CURRENCY_RE.fullmatch(self.currency) is None
        ):
            raise RouteDecisionError("known route decision cost requires currency")
        _validate_decimal(self.amount, field_name="cost amount")
        if self.headroom is not None:
            _validate_decimal(self.headroom, field_name="cost headroom")


@dataclass(frozen=True)
class RouteDecisionLatencyEvidenceV1:
    """Receipt-owned comparable latency projection."""

    comparison_group: str
    p95_milliseconds: int
    evidence_digest: str

    def __post_init__(self) -> None:
        _validate_identity(
            self.comparison_group,
            field_name="latency comparison group",
        )
        if (
            isinstance(self.p95_milliseconds, bool)
            or not isinstance(self.p95_milliseconds, int)
            or self.p95_milliseconds < 0
        ):
            raise RouteDecisionError("latency p95 must be a non-negative integer")
        _validate_digest(self.evidence_digest, field_name="latency evidence digest")


@dataclass(frozen=True)
class RouteDecisionEligibleRouteV1:
    """Receipt-owned evidence for one eligible route."""

    route_id: str
    agent_id: str
    profile_digest: str
    capability_snapshot_digest: str
    account_digest: str
    transport_class: str
    cost: RouteDecisionCostEvidenceV1
    compatibility_grade: str
    policy_priority: int
    explicit_preference_match: bool
    exact_capability_match: bool
    latency: RouteDecisionLatencyEvidenceV1 | None
    rank: int | None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.route_id, "eligible route id"),
            (self.agent_id, "eligible agent id"),
            (self.transport_class, "eligible transport class"),
        ):
            _validate_identity(value, field_name=field_name)
        for value, field_name in (
            (self.profile_digest, "eligible profile digest"),
            (
                self.capability_snapshot_digest,
                "eligible capability snapshot digest",
            ),
            (self.account_digest, "eligible account digest"),
        ):
            _validate_digest(value, field_name=field_name)
        if not isinstance(self.cost, RouteDecisionCostEvidenceV1):
            raise RouteDecisionError("eligible route cost evidence is invalid")
        if self.compatibility_grade not in {"degraded", "ready", "verified"}:
            raise RouteDecisionError("eligible compatibility grade is invalid")
        _validate_non_negative_int(
            self.policy_priority,
            field_name="eligible policy priority",
        )
        if self.latency is not None and not isinstance(
            self.latency, RouteDecisionLatencyEvidenceV1
        ):
            raise RouteDecisionError("eligible latency evidence is invalid")
        if self.rank is not None:
            _validate_non_negative_int(self.rank, field_name="eligible route rank")
            if self.rank == 0:
                raise RouteDecisionError("eligible route rank must be positive")


@dataclass(frozen=True)
class RouteDecisionRejectedRouteV1:
    """Receipt-owned stable rejection evidence."""

    route_id: str
    agent_id: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, field_name="rejected route id")
        _validate_identity(self.agent_id, field_name="rejected agent id")
        if not self.reason_codes:
            raise RouteDecisionError("rejected route requires reason codes")
        for reason in self.reason_codes:
            _validate_identity(reason, field_name="rejection reason code")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise RouteDecisionError("rejected route reason codes must be unique")


@dataclass(frozen=True)
class RouteDecisionOverrideV1:
    """Receipt-owned explicit selection of an eligible route."""

    route_id: str
    reason_code: str
    created_at: str

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, field_name="override route id")
        _validate_identity(self.reason_code, field_name="override reason code")
        _validate_timestamp(self.created_at, field_name="override created_at")


@dataclass(frozen=True)
class RouteDecisionBindingsV1:
    """Content-free facts that a decision must bind immutably."""

    task_digest: str
    context_manifest_digest: str
    project_catalog_digest: str
    launch_profile_digest: str | None
    capability_catalog_digest: str
    cost_policy_digest: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.task_digest, "task digest"),
            (self.context_manifest_digest, "context manifest digest"),
            (self.project_catalog_digest, "project catalog digest"),
            (self.capability_catalog_digest, "capability catalog digest"),
            (self.cost_policy_digest, "cost policy digest"),
        ):
            _validate_digest(value, field_name=field_name)
        if self.launch_profile_digest is not None:
            _validate_digest(
                self.launch_profile_digest,
                field_name="launch profile digest",
            )


@dataclass(frozen=True)
class RouteDecisionReceiptV1:
    """One canonical recommendation receipt with no execution authority."""

    route_decision_id: str
    task_digest: str
    context_manifest_digest: str
    project_catalog_digest: str
    launch_profile_digest: str | None
    capability_catalog_digest: str
    cost_policy_digest: str
    eligible_routes: tuple[RouteDecisionEligibleRouteV1, ...]
    rejected_routes: tuple[RouteDecisionRejectedRouteV1, ...]
    recommended_route_id: str | None
    ranker_id: str
    ranker_version: str
    override: RouteDecisionOverrideV1 | None
    outcome: RouteDecisionOutcome
    created_at: str
    receipt_digest: str
    schema_version: int = ROUTE_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_DECISION_SCHEMA_VERSION:
            raise RouteDecisionError("unsupported route decision schema_version")
        _validate_decision_id(self.route_decision_id)
        RouteDecisionBindingsV1(
            task_digest=self.task_digest,
            context_manifest_digest=self.context_manifest_digest,
            project_catalog_digest=self.project_catalog_digest,
            launch_profile_digest=self.launch_profile_digest,
            capability_catalog_digest=self.capability_catalog_digest,
            cost_policy_digest=self.cost_policy_digest,
        )
        if not all(
            isinstance(item, RouteDecisionEligibleRouteV1)
            for item in self.eligible_routes
        ):
            raise RouteDecisionError("eligible route receipt evidence is invalid")
        if not all(
            isinstance(item, RouteDecisionRejectedRouteV1)
            for item in self.rejected_routes
        ):
            raise RouteDecisionError("rejected route receipt evidence is invalid")
        eligible_ids = tuple(item.route_id for item in self.eligible_routes)
        rejected_ids = tuple(item.route_id for item in self.rejected_routes)
        if len(set((*eligible_ids, *rejected_ids))) != len(eligible_ids) + len(
            rejected_ids
        ):
            raise RouteDecisionError("route receipt contains duplicate route ids")
        if self.recommended_route_id is not None:
            _validate_identity(
                self.recommended_route_id,
                field_name="recommended route id",
            )
            if self.recommended_route_id not in eligible_ids:
                raise RouteDecisionError("receipt recommendation must be eligible")
        _validate_identity(self.ranker_id, field_name="ranker id")
        _validate_identity(self.ranker_version, field_name="ranker version")
        if self.override is not None:
            if not isinstance(self.override, RouteDecisionOverrideV1):
                raise RouteDecisionError("route receipt override is invalid")
            if self.override.route_id != self.recommended_route_id:
                raise RouteDecisionError("receipt override must match recommendation")
        if not isinstance(self.outcome, RouteDecisionOutcome):
            raise RouteDecisionError("route receipt outcome is invalid")
        if self.outcome is RouteDecisionOutcome.RECOMMENDED:
            if self.recommended_route_id is None:
                raise RouteDecisionError("recommended receipt requires a route")
        elif self.recommended_route_id is not None:
            raise RouteDecisionError("non-recommended receipt cannot select a route")
        _validate_timestamp(self.created_at, field_name="receipt created_at")
        _validate_digest(self.receipt_digest, field_name="receipt digest")

    @property
    def bindings(self) -> RouteDecisionBindingsV1:
        """Return the immutable freshness bindings."""
        return RouteDecisionBindingsV1(
            task_digest=self.task_digest,
            context_manifest_digest=self.context_manifest_digest,
            project_catalog_digest=self.project_catalog_digest,
            launch_profile_digest=self.launch_profile_digest,
            capability_catalog_digest=self.capability_catalog_digest,
            cost_policy_digest=self.cost_policy_digest,
        )


def _validate_decision_id(value: object) -> None:
    if not isinstance(value, str) or _DECISION_ID_RE.fullmatch(value) is None:
        raise RouteDecisionError("route decision id is invalid")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise RouteDecisionError(f"{field_name} must be a lowercase sha256 digest")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise RouteDecisionError(f"{field_name} is invalid")


def _validate_decimal(value: object, *, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise RouteDecisionError(f"route decision {field_name} must be non-negative")


def _validate_non_negative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RouteDecisionError(f"{field_name} must be a non-negative integer")


def _validate_timestamp(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise RouteDecisionError(f"{field_name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RouteDecisionError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RouteDecisionError(f"{field_name} must include a timezone")


__all__ = [
    "MAX_ROUTE_DECISION_BYTES",
    "ROUTE_DECISION_SCHEMA_VERSION",
    "RouteDecisionBindingsV1",
    "RouteDecisionConflictError",
    "RouteDecisionCostEvidenceV1",
    "RouteDecisionCostKnowledge",
    "RouteDecisionEligibleRouteV1",
    "RouteDecisionError",
    "RouteDecisionLatencyEvidenceV1",
    "RouteDecisionNotFoundError",
    "RouteDecisionOutcome",
    "RouteDecisionOverrideV1",
    "RouteDecisionReceiptV1",
    "RouteDecisionRejectedRouteV1",
    "RouteDecisionVerificationError",
]
