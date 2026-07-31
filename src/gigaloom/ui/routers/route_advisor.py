"""Route Advisor recommendation, inspection, and manual override APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from gigaloom.execution.api import RouteIntent, RouteRecommendationQueryV1
from gigaloom.review.api import (
    RouteDecisionError,
    RouteDecisionNotFoundError,
    RouteDecisionOverrideError,
    route_decision_receipt_to_dict,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.route_advisor import (
    RouteDecisionResponse,
    RouteOverrideRequest,
    RouteRecommendationRequest,
)
from gigaloom.ui.services.route_advisor import RouteAdvisorWebService


def create_router(service: RouteAdvisorWebService) -> APIRouter:
    """Create the cohesive router without mutating central composition."""
    router = ContractAPIRouter()

    @router.fs_atomic.post(
        "/api/route-decisions/recommend",
        response_model=RouteDecisionResponse,
    )
    def recommend(payload: RouteRecommendationRequest) -> RouteDecisionResponse:
        try:
            receipt = service.recommend(_query(payload))
        except RouteDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _response(receipt)

    @router.fs_read.get(
        "/api/route-decisions/{route_decision_id}",
        response_model=RouteDecisionResponse,
    )
    def inspect(
        route_decision_id: str = Path(min_length=7, max_length=134),
    ) -> RouteDecisionResponse:
        try:
            receipt = service.inspect(route_decision_id)
        except RouteDecisionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RouteDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _response(receipt)

    @router.fs_atomic.post(
        "/api/route-decisions/{route_decision_id}/override",
        response_model=RouteDecisionResponse,
    )
    def override(
        payload: RouteOverrideRequest,
        route_decision_id: str = Path(min_length=7, max_length=134),
    ) -> RouteDecisionResponse:
        try:
            receipt = service.override(
                route_decision_id,
                route_id=payload.route_id,
                reason_code=payload.reason_code,
            )
        except RouteDecisionNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RouteDecisionOverrideError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RouteDecisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _response(receipt)

    return router


def _query(payload: RouteRecommendationRequest) -> RouteRecommendationQueryV1:
    return RouteRecommendationQueryV1(
        project_id=payload.project_id,
        intent=RouteIntent(payload.intent),
        task_digest=payload.task_digest,
        context_manifest_digest=payload.context_manifest_digest,
        required_capabilities=tuple(sorted(set(payload.required_capabilities))),
        required_transport_classes=tuple(
            sorted(set(payload.required_transport_classes))
        ),
        workspace_policy=payload.workspace_policy,
        network_policy=payload.network_policy,
        cost_policy_ref=payload.cost_policy_ref,
        platform=payload.platform,
        launch_profile_id=payload.launch_profile_id,
        preferred_route_id=payload.preferred_route_id,
        required_host_id=payload.required_host_id,
        required_account_digest=payload.required_account_digest,
        require_known_cost=payload.require_known_cost,
        require_sealed_evaluation=payload.require_sealed_evaluation,
        require_session_portability=payload.require_session_portability,
    )


def _response(receipt) -> RouteDecisionResponse:
    return RouteDecisionResponse(
        receipt=route_decision_receipt_to_dict(receipt),
        execution_started=False,
        confirmation_required=True,
    )


__all__ = ["create_router"]
