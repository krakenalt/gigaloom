"""Web routes for reviewed gateway catalog and exact preflight."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.services.gateway_routes import GatewayRouteWebService


def create_router(service: GatewayRouteWebService) -> APIRouter:
    """Expose bounded discovery and execution-free preflight."""
    router = ContractAPIRouter()

    @router.net_read.get("/api/gateway/routes")
    def gateway_routes(refresh: bool = Query(default=False)) -> dict[str, Any]:
        return service.catalog(refresh=refresh)

    @router.net_atomic.post("/api/gateway/routes/{route_id}/preflight")
    def gateway_route_preflight(
        route_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        acknowledgement = payload.get("acknowledgement_id")
        if acknowledgement is not None and not isinstance(acknowledgement, str):
            raise HTTPException(
                status_code=400, detail="gateway acknowledgement is invalid"
            )
        try:
            return service.preflight(
                route_id,
                acknowledgement_id=acknowledgement,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router


__all__ = ["create_router"]
