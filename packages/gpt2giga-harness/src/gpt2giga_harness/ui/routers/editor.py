"""Routes extracted from the FastAPI composition root: editor."""

from __future__ import annotations

from typing import Any
from fastapi import Body, HTTPException, Request
from gpt2giga_harness.ui.services.editor import editor_dry_run as _editor_dry_run
from gpt2giga_harness.ui.services.request_values import (
    optional_int as _optional_int,
    optional_text as _optional_text,
    required_text as _required_text,
)
from gpt2giga_harness.ui.async_execution import ContractAPIRouter
from gpt2giga_harness.editor import (
    build_open_diff_plan,
    build_open_file_plan,
    build_open_terminal_plan,
    build_open_workspace_plan,
    editor_open_plan_to_dict,
    execute_editor_plan,
    workspace_for_run,
)
from gpt2giga_harness.project import (
    load_project_config,
    project_to_dict,
    resolve_project,
)
from gpt2giga_harness.sessions import RunNotFoundError
from gpt2giga_harness.sessions.models import run_to_dict
from fastapi import APIRouter
from gpt2giga_harness.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.proc.post("/api/editor/open-workspace")
    def editor_open_workspace(
        request: Request, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        dry_run = _editor_dry_run(request, payload)
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            loaded = load_project_config(project_context.root)
            command = _optional_text(payload.get("command")) or loaded.editor.command
            plan = build_open_workspace_plan(project_context.root, command=command)
            result = execute_editor_plan(plan, dry_run=dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "editor": editor_open_plan_to_dict(result),
        }

    @router.proc.post("/api/editor/open-file")
    def editor_open_file(
        request: Request, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        dry_run = _editor_dry_run(request, payload)
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            loaded = load_project_config(project_context.root)
            command = _optional_text(payload.get("command")) or loaded.editor.command
            plan = build_open_file_plan(
                project_context.root,
                _required_text(payload.get("path"), "path is required"),
                command=command,
                line=_optional_int(payload.get("line")),
                column=_optional_int(payload.get("column")),
            )
            result = execute_editor_plan(plan, dry_run=dry_run)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "editor": editor_open_plan_to_dict(result),
        }

    @router.proc.post("/api/editor/open-diff")
    def editor_open_diff(
        request: Request, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        dry_run = _editor_dry_run(request, payload)
        try:
            run_id = _required_text(payload.get("run_id"), "run_id is required")
            run = services.session_store.get_run(run_id)
            project_context = resolve_project(
                workspace_for_run(run),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            loaded = load_project_config(project_context.root)
            command = _optional_text(payload.get("command")) or loaded.editor.command
            plan = build_open_diff_plan(
                run, data_dir=services.config.data_dir, command=command
            )
            result = execute_editor_plan(plan, dry_run=dry_run)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run": run_to_dict(run),
            "project": project_to_dict(project_context),
            "editor": editor_open_plan_to_dict(result),
        }

    @router.proc.post("/api/editor/open-terminal")
    def editor_open_terminal(
        request: Request, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        dry_run = _editor_dry_run(request, payload)
        try:
            run_id = _required_text(payload.get("run_id"), "run_id is required")
            run = services.session_store.get_run(run_id)
            workspace = workspace_for_run(run)
            if workspace is None:
                raise ValueError("Run does not have a workspace to open in a terminal.")
            project_context = resolve_project(
                workspace, data_dir=services.config.data_dir, load_config_name=False
            )
            loaded = load_project_config(project_context.root)
            command = (
                _optional_text(payload.get("command")) or loaded.editor.terminal_command
            )
            plan = build_open_terminal_plan(workspace, command=command)
            result = execute_editor_plan(plan, dry_run=dry_run)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "run": run_to_dict(run),
            "project": project_to_dict(project_context),
            "editor": editor_open_plan_to_dict(result),
        }

    return router
