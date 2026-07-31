"""Versioned, provider-neutral Route Advisor evidence models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum, IntEnum
import re


ROUTE_ADVISOR_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CURRENCY_RE = re.compile(r"[A-Z]{3}\Z")


class RouteIntent(str, Enum):
    """Supported deterministic task classes."""

    READ = "read"
    CHANGE = "change"
    REVIEW = "review"
    CHAT = "chat"


class RouteFactState(str, Enum):
    """Truth state for one required admission fact."""

    SATISFIED = "satisfied"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class RouteCostKnowledge(str, Enum):
    """Honest monetary knowledge attached to a route snapshot."""

    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CompatibilityGrade(IntEnum):
    """Reviewed structured-route compatibility grade."""

    DEGRADED = 1
    READY = 2
    VERIFIED = 3


class RouteRejectionCode(str, Enum):
    """Stable fail-closed admission reason codes."""

    PROFILE_NOT_ADMITTED = "profile_not_admitted"
    PROFILE_ADMISSION_UNKNOWN = "profile_admission_unknown"
    STRUCTURED_ROUTE_MISSING = "structured_route_missing"
    STRUCTURED_ROUTE_PRESENCE_UNKNOWN = "structured_route_presence_unknown"
    EXECUTABLE_NOT_READY = "executable_not_ready"
    EXECUTABLE_READINESS_UNKNOWN = "executable_readiness_unknown"
    VERSION_NOT_READY = "version_not_ready"
    VERSION_READINESS_UNKNOWN = "version_readiness_unknown"
    CAPABILITY_SNAPSHOT_STALE = "capability_snapshot_stale"
    CAPABILITY_SNAPSHOT_UNKNOWN = "capability_snapshot_unknown"
    REQUIRED_CAPABILITY_MISSING = "required_capability_missing"
    REQUIRED_CAPABILITY_UNKNOWN = "required_capability_unknown"
    TRANSPORT_CLASS_MISMATCH = "transport_class_mismatch"
    PLATFORM_INCOMPATIBLE = "platform_incompatible"
    HOST_INCOMPATIBLE = "host_incompatible"
    HOST_IDENTITY_UNKNOWN = "host_identity_unknown"
    WORKSPACE_POLICY_INCOMPATIBLE = "workspace_policy_incompatible"
    NETWORK_POLICY_INCOMPATIBLE = "network_policy_incompatible"
    ACCOUNT_IDENTITY_DRIFTED = "account_identity_drifted"
    ACCOUNT_IDENTITY_UNRESOLVED = "account_identity_unresolved"
    POLICY_DENIED = "policy_denied"
    POLICY_UNKNOWN = "policy_unknown"
    BUDGET_NOT_ADMITTED = "budget_not_admitted"
    BUDGET_ADMISSION_UNKNOWN = "budget_admission_unknown"
    MONETARY_COST_UNKNOWN = "monetary_cost_unknown"
    SEALED_EVALUATION_MISSING = "sealed_evaluation_missing"
    SEALED_EVALUATION_UNKNOWN = "sealed_evaluation_unknown"
    PROJECT_LOCATION_UNRESOLVED = "project_location_unresolved"
    PROJECT_LOCATION_UNKNOWN = "project_location_unknown"
    SESSION_PORTABILITY_UNPROVEN = "session_portability_unproven"
    SESSION_PORTABILITY_UNKNOWN = "session_portability_unknown"


class RouteAdviceOutcome(str, Enum):
    """Selection state produced without starting a route."""

    RECOMMENDED = "recommended"
    NEEDS_HUMAN = "needs_human"
    NO_ELIGIBLE_ROUTE = "no_eligible_route"


@dataclass(frozen=True)
class CapabilityEvidence:
    """One capability fact from an immutable capability snapshot."""

    capability_id: str
    state: RouteFactState

    def __post_init__(self) -> None:
        _validate_identity(self.capability_id, field_name="capability id")
        _validate_fact_state(self.state, field_name="capability state")


@dataclass(frozen=True)
class RouteCostEvidence:
    """Content-free monetary evidence without invented zeroes."""

    knowledge: RouteCostKnowledge
    currency: str | None = None
    amount: Decimal | None = None
    headroom: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.knowledge, RouteCostKnowledge):
            raise ValueError("route cost knowledge is invalid")
        if self.knowledge is RouteCostKnowledge.UNKNOWN:
            if any(
                value is not None
                for value in (self.currency, self.amount, self.headroom)
            ):
                raise ValueError("unknown route cost cannot contain monetary values")
            return
        if (
            not isinstance(self.currency, str)
            or _CURRENCY_RE.fullmatch(self.currency) is None
        ):
            raise ValueError("known route cost requires a currency")
        _validate_decimal(self.amount, field_name="route cost amount")
        if self.headroom is not None:
            _validate_decimal(self.headroom, field_name="route cost headroom")


@dataclass(frozen=True)
class LatencyEvidence:
    """Comparable, bounded latency evidence for optional ranking."""

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
            raise ValueError("latency p95 must be a non-negative integer")
        _validate_digest(self.evidence_digest, field_name="latency evidence digest")


@dataclass(frozen=True)
class StructuredRouteCandidateV1:
    """Immutable facts for one admitted structured-route candidate."""

    route_id: str
    agent_id: str
    profile_digest: str
    transport_class: str
    capabilities: tuple[CapabilityEvidence, ...]
    platform_support: tuple[str, ...]
    workspace_policies: tuple[str, ...]
    network_policies: tuple[str, ...]
    profile_admission: RouteFactState
    route_presence: RouteFactState
    executable_readiness: RouteFactState
    version_readiness: RouteFactState
    capability_snapshot_state: RouteFactState
    capability_snapshot_digest: str
    account_state: RouteFactState
    account_digest: str | None
    policy_state: RouteFactState
    budget_state: RouteFactState
    project_location_state: RouteFactState
    sealed_evaluation_state: RouteFactState
    session_portability_state: RouteFactState
    cost: RouteCostEvidence
    compatibility_grade: CompatibilityGrade
    policy_priority: int
    host_id: str | None = None
    latency: LatencyEvidence | None = None
    schema_version: int = ROUTE_ADVISOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_ADVISOR_SCHEMA_VERSION:
            raise ValueError("unsupported route candidate schema_version")
        for value, field_name in (
            (self.route_id, "route id"),
            (self.agent_id, "agent id"),
            (self.transport_class, "transport class"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(self.profile_digest, field_name="profile digest")
        _validate_identity_sequence(
            self.platform_support,
            field_name="platform support",
        )
        _validate_identity_sequence(
            self.workspace_policies,
            field_name="workspace policies",
        )
        _validate_identity_sequence(
            self.network_policies,
            field_name="network policies",
        )
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        if not all(isinstance(item, CapabilityEvidence) for item in self.capabilities):
            raise ValueError("route capabilities are invalid")
        if capability_ids != tuple(sorted(set(capability_ids))):
            raise ValueError("route capabilities must be sorted and unique")
        for value, field_name in (
            (self.profile_admission, "profile admission"),
            (self.route_presence, "route presence"),
            (self.executable_readiness, "executable readiness"),
            (self.version_readiness, "version readiness"),
            (self.capability_snapshot_state, "capability snapshot state"),
            (self.account_state, "account state"),
            (self.policy_state, "policy state"),
            (self.budget_state, "budget state"),
            (self.project_location_state, "project location state"),
            (self.sealed_evaluation_state, "sealed evaluation state"),
            (self.session_portability_state, "session portability state"),
        ):
            _validate_fact_state(value, field_name=field_name)
        _validate_digest(
            self.capability_snapshot_digest,
            field_name="capability snapshot digest",
        )
        if self.account_digest is not None:
            _validate_digest(self.account_digest, field_name="account digest")
        if (
            self.account_state is RouteFactState.SATISFIED
            and self.account_digest is None
        ):
            raise ValueError("resolved account state requires an account digest")
        if self.host_id is not None:
            _validate_identity(self.host_id, field_name="host id")
        if not isinstance(self.cost, RouteCostEvidence):
            raise ValueError("route cost evidence is invalid")
        if not isinstance(self.compatibility_grade, CompatibilityGrade):
            raise ValueError("route compatibility grade is invalid")
        if (
            isinstance(self.policy_priority, bool)
            or not isinstance(self.policy_priority, int)
            or self.policy_priority < 0
        ):
            raise ValueError("route policy priority must be a non-negative integer")
        if self.latency is not None and not isinstance(self.latency, LatencyEvidence):
            raise ValueError("route latency evidence is invalid")


@dataclass(frozen=True)
class RouteAdmissionEvaluation:
    """Deterministic admission result for one candidate."""

    candidate: StructuredRouteCandidateV1
    rejection_codes: tuple[RouteRejectionCode, ...]

    @property
    def eligible(self) -> bool:
        """Return whether every required fact was satisfied."""
        return not self.rejection_codes


@dataclass(frozen=True)
class EligibleRouteEvidence:
    """Content-free evidence and rank inputs for one eligible route."""

    route_id: str
    agent_id: str
    profile_digest: str
    capability_snapshot_digest: str
    account_digest: str
    transport_class: str
    cost: RouteCostEvidence
    compatibility_grade: CompatibilityGrade
    policy_priority: int
    explicit_preference_match: bool
    exact_capability_match: bool
    latency: LatencyEvidence | None
    rank: int | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.route_id, "eligible route id"),
            (self.agent_id, "eligible agent id"),
            (self.transport_class, "eligible transport class"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(self.profile_digest, field_name="eligible profile digest")
        _validate_digest(
            self.capability_snapshot_digest,
            field_name="eligible capability snapshot digest",
        )
        _validate_digest(self.account_digest, field_name="eligible account digest")
        if not isinstance(self.cost, RouteCostEvidence):
            raise ValueError("eligible route cost evidence is invalid")
        if not isinstance(self.compatibility_grade, CompatibilityGrade):
            raise ValueError("eligible compatibility grade is invalid")
        if (
            isinstance(self.policy_priority, bool)
            or not isinstance(self.policy_priority, int)
            or self.policy_priority < 0
        ):
            raise ValueError("eligible policy priority is invalid")
        if self.latency is not None and not isinstance(self.latency, LatencyEvidence):
            raise ValueError("eligible latency evidence is invalid")
        if self.rank is not None and (
            isinstance(self.rank, bool)
            or not isinstance(self.rank, int)
            or self.rank < 1
        ):
            raise ValueError("eligible route rank is invalid")


@dataclass(frozen=True)
class RejectedRouteEvidence:
    """Stable rejection evidence for one ineligible route."""

    route_id: str
    agent_id: str
    reason_codes: tuple[RouteRejectionCode, ...]

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, field_name="rejected route id")
        _validate_identity(self.agent_id, field_name="rejected agent id")
        if not self.reason_codes or not all(
            isinstance(item, RouteRejectionCode) for item in self.reason_codes
        ):
            raise ValueError("rejected route requires reason codes")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("rejected route reason codes must be unique")


@dataclass(frozen=True)
class RouteOverrideV1:
    """Content-free explicit selection of an eligible route."""

    route_id: str
    reason_code: str
    created_at: str

    def __post_init__(self) -> None:
        _validate_identity(self.route_id, field_name="override route id")
        _validate_identity(self.reason_code, field_name="override reason code")
        _validate_timestamp(self.created_at, field_name="override created_at")


@dataclass(frozen=True)
class RouteAdviceV1:
    """Deterministic recommendation result with no execution authority."""

    eligible_routes: tuple[EligibleRouteEvidence, ...]
    rejected_routes: tuple[RejectedRouteEvidence, ...]
    recommended_route_id: str | None
    ranker_id: str
    ranker_version: str
    outcome: RouteAdviceOutcome
    override: RouteOverrideV1 | None = None
    schema_version: int = ROUTE_ADVISOR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ROUTE_ADVISOR_SCHEMA_VERSION:
            raise ValueError("unsupported route advice schema_version")
        if not all(
            isinstance(item, EligibleRouteEvidence) for item in self.eligible_routes
        ):
            raise ValueError("eligible route evidence is invalid")
        if not all(
            isinstance(item, RejectedRouteEvidence) for item in self.rejected_routes
        ):
            raise ValueError("rejected route evidence is invalid")
        eligible_ids = tuple(item.route_id for item in self.eligible_routes)
        rejected_ids = tuple(item.route_id for item in self.rejected_routes)
        if len(set((*eligible_ids, *rejected_ids))) != len(eligible_ids) + len(
            rejected_ids
        ):
            raise ValueError("route advice contains duplicate route ids")
        if self.recommended_route_id is not None:
            _validate_identity(
                self.recommended_route_id,
                field_name="recommended route id",
            )
            if self.recommended_route_id not in eligible_ids:
                raise ValueError("recommended route must be eligible")
        _validate_identity(self.ranker_id, field_name="ranker id")
        _validate_identity(self.ranker_version, field_name="ranker version")
        if not isinstance(self.outcome, RouteAdviceOutcome):
            raise ValueError("route advice outcome is invalid")
        if self.outcome is RouteAdviceOutcome.RECOMMENDED:
            if self.recommended_route_id is None:
                raise ValueError("recommended outcome requires a route")
        elif self.recommended_route_id is not None:
            raise ValueError("non-recommended outcome cannot select a route")
        if self.override is not None:
            if not isinstance(self.override, RouteOverrideV1):
                raise ValueError("route override is invalid")
            if self.override.route_id != self.recommended_route_id:
                raise ValueError("route override must match the selected route")


def _validate_fact_state(value: object, *, field_name: str) -> None:
    if not isinstance(value, RouteFactState):
        raise ValueError(f"{field_name} is invalid")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_identity_sequence(value: tuple[str, ...], *, field_name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    for item in value:
        _validate_identity(item, field_name=field_name)
    if value != tuple(sorted(set(value))):
        raise ValueError(f"{field_name} must be sorted and unique")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_decimal(value: Decimal | None, *, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative Decimal")


def _validate_timestamp(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")


__all__ = [
    "CapabilityEvidence",
    "CompatibilityGrade",
    "EligibleRouteEvidence",
    "LatencyEvidence",
    "ROUTE_ADVISOR_SCHEMA_VERSION",
    "RouteAdmissionEvaluation",
    "RouteAdviceOutcome",
    "RouteAdviceV1",
    "RouteCostEvidence",
    "RouteCostKnowledge",
    "RouteFactState",
    "RouteIntent",
    "RouteOverrideV1",
    "RejectedRouteEvidence",
    "RouteRejectionCode",
    "StructuredRouteCandidateV1",
]
