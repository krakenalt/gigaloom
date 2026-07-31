"""Routes extracted from the FastAPI composition root: project memory."""

from __future__ import annotations

from typing import Any
from fastapi import Body, HTTPException, Query
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
    text_tuple as _text_tuple,
)
from gigaloom.ui.services.request_values import (
    optional_float as _optional_float,
    optional_text as _optional_text,
    required_text as _required_text,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.project import project_to_dict, resolve_project
from gigaloom.project_memory import (
    ProjectMemoryNotFoundError,
    memory_entry_to_dict,
)
from fastapi import APIRouter
from gigaloom.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.fs_read.get("/api/project/memory")
    def project_memory(
        workspace: str | None = Query(default=None),
        include_disabled: bool = Query(default=True),
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        memories = services.project_memory_store.list(
            project_context, include_disabled=include_disabled
        )
        return {
            "project": project_to_dict(project_context),
            "memories": [memory_entry_to_dict(entry) for entry in memories],
        }

    @router.fs_atomic.post("/api/project/memory")
    def add_project_memory(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            memory = services.project_memory_store.add(
                project_context,
                text=_required_text(payload.get("text"), "memory text is required"),
                tags=_text_tuple(payload.get("tags")),
                source_session_id=_optional_text(payload.get("source_session_id")),
                source_run_id=_optional_text(payload.get("source_run_id")),
                enabled=bool(payload.get("enabled", True)),
                manual=bool(payload.get("manual", True)),
                confidence=_optional_float(payload.get("confidence")),
                metadata=_metadata_mapping(payload.get("metadata")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "memory": memory_entry_to_dict(memory),
        }

    @router.fs_atomic.patch("/api/project/memory/{memory_id}")
    def update_project_memory(
        memory_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(payload.get("workspace")),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            update_kwargs: dict[str, Any] = {
                "text": _optional_text(payload.get("text")),
                "tags": _text_tuple(payload.get("tags")) if "tags" in payload else None,
                "enabled": bool(payload["enabled"]) if "enabled" in payload else None,
                "manual": bool(payload["manual"]) if "manual" in payload else None,
            }
            if "confidence" in payload:
                update_kwargs["confidence"] = _optional_float(payload.get("confidence"))
            if "metadata" in payload:
                update_kwargs["metadata"] = _metadata_mapping(payload.get("metadata"))
            memory = services.project_memory_store.update(
                project_context, memory_id, **update_kwargs
            )
        except ProjectMemoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Memory not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "memory": memory_entry_to_dict(memory),
        }

    @router.fs_atomic.delete("/api/project/memory/{memory_id}")
    def delete_project_memory(
        memory_id: str, workspace: str | None = Query(default=None)
    ) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            services.project_memory_store.delete(project_context, memory_id)
        except ProjectMemoryNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Memory not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": True, "project": project_to_dict(project_context)}

    return router
