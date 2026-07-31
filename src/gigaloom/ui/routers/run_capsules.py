"""Run Capsule integrity evidence and verified archive download routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from gigaloom.review.api import CapsuleError
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.services.operator_workspace import operator_scope
from gigaloom.ui.services.run_capsules import RunCapsuleEvidenceQuery


def create_router(query: RunCapsuleEvidenceQuery) -> APIRouter:
    """Create the unmounted B5 route family for F0 composition."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/operator/runs/{run_id}/capsule")
    def capsule_evidence(
        run_id: str,
        request: Request,
        workspace_id: str = Query(...),
    ) -> dict[str, object]:
        owner_id, workspace_id = operator_scope(request, workspace_id)
        try:
            evidence = query.get(
                run_id=run_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "capsule_not_found"},
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "capsule_forbidden"},
            ) from exc
        except CapsuleError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "capsule_integrity_failed"},
            ) from exc
        return {"capsule": evidence.model_dump(mode="json")}

    @router.fs_read.get("/api/operator/runs/{run_id}/capsule/export")
    def export_capsule(
        run_id: str,
        request: Request,
        workspace_id: str = Query(...),
    ) -> FileResponse:
        owner_id, workspace_id = operator_scope(request, workspace_id)
        try:
            archive = query.export_path(
                run_id=run_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "capsule_not_found"},
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail={"code": "capsule_forbidden"},
            ) from exc
        except CapsuleError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "capsule_integrity_failed"},
            ) from exc
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=archive.name,
        )

    return router


__all__ = ["create_router"]
