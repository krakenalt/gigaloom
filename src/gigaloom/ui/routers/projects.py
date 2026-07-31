"""Routes extracted from the FastAPI composition root: projects."""

from __future__ import annotations

from typing import Any
from fastapi import Body, HTTPException, Query
from gigaloom.ui.services.attachments import text_tuple as _text_tuple
from gigaloom.ui.services.projects import project_response as _project_response
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.project import (
    init_project_config,
    load_project_state,
    load_project_config,
    project_config_to_dict,
    project_preset_to_dict,
    project_state_to_dict,
    project_to_dict,
    render_project_preset,
    rendered_project_preset_to_dict,
    resolve_project,
    update_project_state,
)
from fastapi import APIRouter
from gigaloom.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.fs_read.get("/api/project")
    def project(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            return _project_response(
                workspace=_optional_text(workspace), data_dir=services.config.data_dir
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.fs_read.get("/api/project/config")
    def project_config(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace), data_dir=services.config.data_dir
            )
            loaded = load_project_config(project_context.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"config": project_config_to_dict(loaded)}

    @router.fs_read.get("/api/project/presets")
    def project_presets(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace), data_dir=services.config.data_dir
            )
            loaded = load_project_config(project_context.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "presets": [
                project_preset_to_dict(name, preset)
                for name, preset in loaded.presets.items()
            ],
        }

    @router.fs_read.post("/api/project/presets/{preset_name}/render")
    def render_preset(
        preset_name: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
            )
            loaded = load_project_config(project_context.root)
            rendered = render_project_preset(
                project_context,
                loaded,
                preset_name,
                user_prompt=_optional_text(payload.get("user_prompt")),
                selected_files=_text_tuple(payload.get("selected_files")),
                last_run_diff=_optional_text(payload.get("last_run_diff")),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Preset not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "preset": rendered_project_preset_to_dict(rendered),
        }

    @router.fs_read.get("/api/project/state")
    def project_state(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace), data_dir=services.config.data_dir
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "state": project_state_to_dict(load_project_state(project_context)),
        }

    @router.fs_atomic.patch("/api/project/state")
    def update_state(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            state = update_project_state(project_context, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "state": project_state_to_dict(state),
        }

    @router.proc.post("/api/project/init")
    def project_init(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            init_project_config(
                project_context.root,
                project_name=_optional_text(payload.get("name")),
                overwrite=bool(payload.get("overwrite")),
            )
            return _project_response(
                workspace=project_context.root, data_dir=services.config.data_dir
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
