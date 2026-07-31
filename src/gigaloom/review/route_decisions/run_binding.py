"""Fail-closed route receipt revalidation immediately before execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, TypeVar

from gigaloom.review.route_decisions.models import (
    RouteDecisionBindingsV1,
    RouteDecisionCostEvidenceV1,
    RouteDecisionOutcome,
    RouteDecisionReceiptV1,
    RouteRunBindingError,
    _validate_digest,
    _validate_identity,
    _validate_timestamp,
)
from gigaloom.review.route_decisions.verification import (
    verify_route_decision_receipt,
)


@dataclass(frozen=True)
class RouteRunConfirmationV1:
    """Explicit operator confirmation bound to one immutable selection."""

    confirmation_id: str
    operator_id: str
    route_decision_id: str
    receipt_digest: str
    route_id: str
    confirmed_at: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.confirmation_id, "route confirmation id"),
            (self.operator_id, "route confirmation operator id"),
            (self.route_decision_id, "route confirmation decision id"),
            (self.route_id, "route confirmation route id"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(
            self.receipt_digest,
            field_name="route confirmation receipt digest",
        )
        _validate_timestamp(
            self.confirmed_at,
            field_name="route confirmation timestamp",
        )


@dataclass(frozen=True)
class CurrentRouteRunEvidenceV1:
    """Current server-owned route facts loaded at the execution boundary."""

    bindings: RouteDecisionBindingsV1
    route_id: str
    agent_id: str
    profile_digest: str
    capability_snapshot_digest: str
    account_digest: str
    transport_class: str
    cost: RouteDecisionCostEvidenceV1
    eligible: bool
    rejection_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, RouteDecisionBindingsV1):
            raise RouteRunBindingError("current route bindings are invalid")
        for value, field_name in (
            (self.route_id, "current route id"),
            (self.agent_id, "current route agent id"),
            (self.transport_class, "current route transport class"),
        ):
            _validate_identity(value, field_name=field_name)
        for value, field_name in (
            (self.profile_digest, "current route profile digest"),
            (
                self.capability_snapshot_digest,
                "current route capability snapshot digest",
            ),
            (self.account_digest, "current route account digest"),
        ):
            _validate_digest(value, field_name=field_name)
        if not isinstance(self.cost, RouteDecisionCostEvidenceV1):
            raise RouteRunBindingError("current route cost evidence is invalid")
        if not isinstance(self.eligible, bool):
            raise RouteRunBindingError("current route eligibility is invalid")
        if self.eligible and self.rejection_codes:
            raise RouteRunBindingError(
                "eligible current route cannot contain rejection codes"
            )
        if not self.eligible and not self.rejection_codes:
            raise RouteRunBindingError(
                "ineligible current route requires rejection codes"
            )
        for code in self.rejection_codes:
            _validate_identity(code, field_name="current route rejection code")
        if len(self.rejection_codes) != len(set(self.rejection_codes)):
            raise RouteRunBindingError("current route rejection codes must be unique")


@dataclass(frozen=True)
class ConfirmedRouteRunPlanV1:
    """Exact structured route admitted for one execution attempt."""

    route_decision_id: str
    receipt_digest: str
    confirmation_id: str
    operator_id: str
    route_id: str
    agent_id: str
    profile_digest: str
    capability_snapshot_digest: str
    account_digest: str
    transport_class: str
    fallback_allowed: Literal[False] = False
    execution_attempts: Literal[1] = 1

    def __post_init__(self) -> None:
        if self.fallback_allowed is not False or self.execution_attempts != 1:
            raise RouteRunBindingError("confirmed route plan cannot enable fallback")


class CurrentRouteRunEvidenceSource(Protocol):
    """Load current facts once at the immediate execution boundary."""

    def current_evidence(
        self,
        *,
        route_decision_id: str,
        route_id: str,
    ) -> CurrentRouteRunEvidenceV1: ...


RequestT = TypeVar("RequestT", contravariant=True)
ResultT = TypeVar("ResultT", covariant=True)


class ExactStructuredRouteRunner(Protocol[RequestT, ResultT]):
    """Execute only the exact route already fixed in the confirmed plan."""

    def run(
        self,
        plan: ConfirmedRouteRunPlanV1,
        request: RequestT,
    ) -> ResultT: ...


def revalidate_route_decision_for_run(
    receipt: RouteDecisionReceiptV1,
    *,
    current: CurrentRouteRunEvidenceV1,
    confirmation: RouteRunConfirmationV1,
) -> ConfirmedRouteRunPlanV1:
    """Reject stale facts or mismatched confirmation before process start."""
    verified = verify_route_decision_receipt(
        receipt,
        expected_bindings=current.bindings,
    )
    route_id = verified.recommended_route_id
    if verified.outcome is not RouteDecisionOutcome.RECOMMENDED or route_id is None:
        raise RouteRunBindingError("route decision has no confirmed recommendation")
    selected = next(
        (item for item in verified.eligible_routes if item.route_id == route_id),
        None,
    )
    if selected is None:
        raise RouteRunBindingError("selected route is not eligible in the receipt")
    if not current.eligible:
        raise RouteRunBindingError(
            "selected route is no longer eligible: " + ",".join(current.rejection_codes)
        )
    _verify_confirmation(verified, confirmation)
    stale_fields = tuple(
        field_name
        for field_name in (
            "route_id",
            "agent_id",
            "profile_digest",
            "capability_snapshot_digest",
            "account_digest",
            "transport_class",
            "cost",
        )
        if getattr(selected, field_name) != getattr(current, field_name)
    )
    if stale_fields:
        raise RouteRunBindingError(
            "selected route evidence is stale: " + ",".join(stale_fields)
        )
    return ConfirmedRouteRunPlanV1(
        route_decision_id=verified.route_decision_id,
        receipt_digest=verified.receipt_digest,
        confirmation_id=confirmation.confirmation_id,
        operator_id=confirmation.operator_id,
        route_id=selected.route_id,
        agent_id=selected.agent_id,
        profile_digest=selected.profile_digest,
        capability_snapshot_digest=selected.capability_snapshot_digest,
        account_digest=selected.account_digest,
        transport_class=selected.transport_class,
    )


def execute_confirmed_route(
    receipt: RouteDecisionReceiptV1,
    *,
    confirmation: RouteRunConfirmationV1,
    request: RequestT,
    evidence_source: CurrentRouteRunEvidenceSource,
    runner: ExactStructuredRouteRunner[RequestT, ResultT],
) -> ResultT:
    """Revalidate once, then call exactly one route runner without fallback."""
    route_id = receipt.recommended_route_id
    if route_id is None:
        raise RouteRunBindingError("route decision has no selected route")
    current = evidence_source.current_evidence(
        route_decision_id=receipt.route_decision_id,
        route_id=route_id,
    )
    plan = revalidate_route_decision_for_run(
        receipt,
        current=current,
        confirmation=confirmation,
    )
    return runner.run(plan, request)


def _verify_confirmation(
    receipt: RouteDecisionReceiptV1,
    confirmation: RouteRunConfirmationV1,
) -> None:
    mismatches = tuple(
        field_name
        for field_name in (
            "route_decision_id",
            "receipt_digest",
            "route_id",
        )
        if getattr(confirmation, field_name)
        != (
            receipt.recommended_route_id
            if field_name == "route_id"
            else getattr(receipt, field_name)
        )
    )
    if mismatches:
        raise RouteRunBindingError(
            "route confirmation does not match receipt: " + ",".join(mismatches)
        )
    if _parse_timestamp(confirmation.confirmed_at) < _parse_timestamp(
        receipt.created_at
    ):
        raise RouteRunBindingError("route confirmation predates the receipt")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


__all__ = [
    "ConfirmedRouteRunPlanV1",
    "CurrentRouteRunEvidenceSource",
    "CurrentRouteRunEvidenceV1",
    "ExactStructuredRouteRunner",
    "RouteRunConfirmationV1",
    "execute_confirmed_route",
    "revalidate_route_decision_for_run",
]
