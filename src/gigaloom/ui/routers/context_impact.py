"""Bounded read-only Context Lens and Impact Radar route family."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from gigaloom.execution.api import NativeCodexContextProjection
from gigaloom.projects.api import StalePythonImpactIndexError
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.operator_workspace import operator_scope


MAX_IMPACT_CHANGED_PATHS = 256


def create_router(services: AppServices) -> APIRouter:
    """Create read-only Context Lens and Impact Radar endpoints."""
    router = ContractAPIRouter()

    @router.db_read.get("/api/operator/sessions/{session_id}/context")
    def native_session_context(
        session_id: str,
        request: Request,
        workspace_id: str = Query(...),
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, workspace_id)
        provider = services.context_projection_query
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail=_detail(
                    "context_unavailable",
                    "Context projection owner is unavailable",
                ),
            )
        try:
            projection = provider.get_native_codex_context(
                session_id=session_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("context_not_found", "Session context was not found"),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("context_forbidden", "Session context is forbidden"),
            ) from exc
        if not isinstance(projection, NativeCodexContextProjection):
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "context_contract_invalid",
                    "Context owner returned an invalid projection",
                ),
            )
        if projection.binding.harness_session_id != session_id:
            raise HTTPException(
                status_code=403,
                detail=_detail(
                    "context_binding_mismatch",
                    "Session context binding does not match",
                ),
            )
        return {"context": projection.to_dict()}

    @router.proc.get("/api/project/impact")
    def project_impact(
        workspace: str = Query(..., min_length=1, max_length=4096),
        changed_path: list[str] = Query(...),
        index_digest: str | None = Query(
            default=None,
            min_length=64,
            max_length=64,
            pattern="[0-9a-f]{64}",
        ),
        source_revision: str | None = Query(default=None, min_length=1, max_length=256),
    ) -> dict[str, object]:
        if not 1 <= len(changed_path) <= MAX_IMPACT_CHANGED_PATHS or any(
            not path or len(path) > 4096 for path in changed_path
        ):
            raise HTTPException(
                status_code=422,
                detail=_detail(
                    "invalid_impact_request",
                    f"changed_path must contain 1..{MAX_IMPACT_CHANGED_PATHS} items",
                ),
            )
        try:
            outcome = services.impact_projection_service.project(
                workspace=workspace,
                changed_paths=changed_path,
                expected_index_digest=index_digest,
                expected_source_revision=source_revision,
            )
        except StalePythonImpactIndexError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail("resnapshot_required", str(exc)),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=_detail("invalid_impact_request", str(exc)),
            ) from exc
        return {
            "impact": outcome.projection.to_dict(),
            "cache_hit": outcome.cache_hit,
        }

    return router


def _scope(request: Request, workspace_id: object) -> tuple[str, str]:
    try:
        return operator_scope(request, workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_scope", "Operator scope is invalid"),
        ) from exc


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


__all__ = ["MAX_IMPACT_CHANGED_PATHS", "create_router"]
