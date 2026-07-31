"""Bounded Web schemas for governed route decisions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Digest = str
Identity = str


class RouteRecommendationRequest(BaseModel):
    """Deterministic requirements; current route facts remain server-owned."""

    project_id: Identity = Field(min_length=1, max_length=256)
    intent: Literal["read", "change", "review", "chat"]
    task_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    context_manifest_digest: Digest = Field(pattern=r"^[0-9a-f]{64}$")
    required_capabilities: list[Identity] = Field(default_factory=list, max_length=64)
    required_transport_classes: list[Identity] = Field(
        default_factory=list,
        max_length=16,
    )
    workspace_policy: Identity = Field(default="read_only", max_length=256)
    network_policy: Identity = Field(default="denied", max_length=256)
    cost_policy_ref: Identity = Field(default="explicit_unlimited", max_length=256)
    platform: Identity = Field(min_length=1, max_length=128)
    launch_profile_id: Identity | None = Field(default=None, max_length=256)
    preferred_route_id: Identity | None = Field(default=None, max_length=256)
    required_host_id: Identity | None = Field(default=None, max_length=256)
    required_account_digest: Digest | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    require_known_cost: bool = False
    require_sealed_evaluation: bool = False
    require_session_portability: bool = False


class RouteOverrideRequest(BaseModel):
    """Explicit eligible route selection with a stable reason code."""

    route_id: Identity = Field(min_length=1, max_length=256)
    reason_code: Identity = Field(min_length=1, max_length=256)


class RouteCostResponse(BaseModel):
    knowledge: Literal["exact", "estimated", "unknown"]
    currency: str | None
    amount: str | None
    headroom: str | None


class RouteLatencyResponse(BaseModel):
    comparison_group: str
    p95_milliseconds: int = Field(ge=0)
    evidence_digest: Digest


class EligibleRouteResponse(BaseModel):
    route_id: str
    agent_id: str
    profile_digest: Digest
    capability_snapshot_digest: Digest
    account_digest: Digest
    transport_class: str
    cost: RouteCostResponse
    compatibility_grade: Literal["degraded", "ready", "verified"]
    policy_priority: int = Field(ge=0)
    explicit_preference_match: bool
    exact_capability_match: bool
    latency: RouteLatencyResponse | None
    rank: int | None = Field(default=None, ge=1)


class RejectedRouteResponse(BaseModel):
    route_id: str
    agent_id: str
    reason_codes: list[str] = Field(min_length=1, max_length=64)


class RouteOverrideResponse(BaseModel):
    route_id: str
    reason_code: str
    created_at: str


class RouteDecisionReceiptResponse(BaseModel):
    schema_version: Literal[1]
    route_decision_id: str
    task_digest: Digest
    context_manifest_digest: Digest
    project_catalog_digest: Digest
    launch_profile_digest: Digest | None
    capability_catalog_digest: Digest
    cost_policy_digest: Digest
    eligible_routes: list[EligibleRouteResponse]
    rejected_routes: list[RejectedRouteResponse]
    recommended_route_id: str | None
    ranker_id: str
    ranker_version: str
    override: RouteOverrideResponse | None
    outcome: Literal["recommended", "needs_human", "no_eligible_route"]
    created_at: str
    receipt_digest: Digest


class RouteDecisionResponse(BaseModel):
    """Inspection contract that explicitly grants no execution authority."""

    receipt: RouteDecisionReceiptResponse
    execution_started: Literal[False] = False
    confirmation_required: Literal[True] = True


__all__ = [
    "RouteDecisionResponse",
    "RouteOverrideRequest",
    "RouteRecommendationRequest",
]
