"""Routes extracted from the FastAPI composition root: project tools."""

from __future__ import annotations

from typing import Any
from fastapi import Body, HTTPException, Query
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.project import (
    load_project_config,
    project_to_dict,
    resolve_project,
)
from gigaloom.tool_profiles import (
    build_tool_profile_statuses,
    tool_profile_status_to_dict,
)
from fastapi import APIRouter
from gigaloom.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.fs_read.get("/api/tools")
    def tools(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace), data_dir=services.config.data_dir
            )
            loaded = load_project_config(project_context.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        statuses = build_tool_profile_statuses(
            loaded.tool_profiles, services.registry, include_previews=False
        )
        return {
            "project": project_to_dict(project_context),
            "profiles": [tool_profile_status_to_dict(status) for status in statuses],
        }

    @router.proc_read.post("/api/tools/sync")
    def tools_sync(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
            )
            loaded = load_project_config(project_context.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        statuses = build_tool_profile_statuses(
            loaded.tool_profiles, services.registry, include_previews=True
        )
        return {
            "dry_run": True,
            "project": project_to_dict(project_context),
            "profiles": [tool_profile_status_to_dict(status) for status in statuses],
        }

    return router
