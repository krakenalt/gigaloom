"""Read-only coding-agent inventory and explicit ACP Registry refresh API."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.agent_registry import AgentRegistryInventoryResponse
from gigaloom.ui.services.agent_registry import AgentRegistryWebService


def create_router(service: AgentRegistryWebService) -> APIRouter:
    """Create the cohesive registry router without central composition edits."""
    router = ContractAPIRouter()

    @router.net_read.get(
        "/api/agent-runtimes/inventory",
        response_model=AgentRegistryInventoryResponse,
    )
    def inventory(
        query: str = Query(default="", max_length=256),
        refresh: bool = Query(default=False),
        platform: str | None = Query(default=None, max_length=128),
        distribution: str | None = Query(
            default=None,
            pattern=r"^(binary|npx|uvx)$",
        ),
        integrity: str | None = Query(
            default=None,
            pattern=r"^(verified|unverified|mixed)$",
        ),
        license_name: str | None = Query(default=None, max_length=128),
    ) -> AgentRegistryInventoryResponse:
        try:
            value = service.inventory(
                query,
                refresh=refresh,
                platform=platform,
                distribution=distribution,
                integrity=integrity,
                license_name=license_name,
            )
        except (OSError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=503,
                detail="ACP Registry inventory is unavailable",
            ) from error
        return AgentRegistryInventoryResponse(
            **asdict(value),
            install_decisions_browser_owned=False,
        )

    return router


__all__ = ["create_router"]
