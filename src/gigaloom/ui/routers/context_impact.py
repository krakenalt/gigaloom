"""Bounded read-only Context Lens and Impact Radar route family."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from gigaloom.execution.api import NativeCodexContextProjection
from gigaloom.projects.api import StalePythonImpactIndexError, instructions_api
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.context_impact import (
    StaleEffectiveInstructionsProjectionError,
)
from gigaloom.ui.services.operator_workspace import operator_scope


MAX_IMPACT_CHANGED_PATHS = 256
MAX_INSTRUCTION_PAGE_SIZE = 100
MAX_INSTRUCTION_REVISION_BINDINGS = 32


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

    @router.proc.get("/api/project/effective-instructions")
    def effective_instructions_summary(
        workspace: str = Query(..., min_length=1, max_length=4096),
        target_path: str = Query(default="", max_length=256),
        materialization_owner: list[str] | None = Query(default=None),
        selected_source_id: list[str] | None = Query(default=None),
        materialization_revision: list[str] | None = Query(default=None),
        expected_materialization_revision: list[str] | None = Query(default=None),
        cursor: int = Query(default=0, ge=0, le=10_000),
        limit: int = Query(default=50, ge=1, le=MAX_INSTRUCTION_PAGE_SIZE),
    ) -> dict[str, object]:
        projection = _effective_instructions(
            services,
            workspace=workspace,
            target_path=target_path,
            materialization_owners=materialization_owner,
            selected_source_ids=selected_source_id,
            materialization_revisions=materialization_revision,
            expected_materialization_revisions=expected_materialization_revision,
        )
        if cursor > len(projection.sources):
            raise HTTPException(
                status_code=422,
                detail=_detail(
                    "invalid_instruction_cursor",
                    "Instruction cursor exceeds the current source count",
                ),
            )
        end = min(cursor + limit, len(projection.sources))
        return {
            "effective_instructions": _instruction_projection_summary(projection),
            "sources": [item.to_dict() for item in projection.sources[cursor:end]],
            "cursor": cursor,
            "next_cursor": end if end < len(projection.sources) else None,
        }

    @router.proc.get("/api/project/effective-instructions/{source_id}")
    def effective_instruction_detail(
        source_id: str,
        workspace: str = Query(..., min_length=1, max_length=4096),
        discovery_digest: str = Query(
            ...,
            min_length=64,
            max_length=64,
            pattern="[0-9a-f]{64}",
        ),
        target_path: str = Query(default="", max_length=256),
        materialization_owner: list[str] | None = Query(default=None),
        selected_source_id: list[str] | None = Query(default=None),
        materialization_revision: list[str] | None = Query(default=None),
        expected_materialization_revision: list[str] | None = Query(default=None),
    ) -> dict[str, object]:
        projection = _effective_instructions(
            services,
            workspace=workspace,
            target_path=target_path,
            materialization_owners=materialization_owner,
            selected_source_ids=selected_source_id,
            materialization_revisions=materialization_revision,
            expected_materialization_revisions=expected_materialization_revision,
            expected_discovery_digest=discovery_digest,
        )
        source = next(
            (item for item in projection.sources if item.source_id == source_id),
            None,
        )
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=_detail(
                    "instruction_source_not_found",
                    "Instruction source was not found",
                ),
            )
        related_conflicts = tuple(
            item for item in projection.conflicts if source_id in item.source_ids
        )
        related_uncertainties = tuple(
            item
            for item in projection.uncertainties
            if item.source_id in {None, source_id}
            and item.materialization_owner
            in {None, source.materialization_owner, source.selector_id}
        )
        return {
            "effective_instructions": _instruction_projection_summary(projection),
            "source": source.to_dict(),
            "conflicts": [item.to_dict() for item in related_conflicts],
            "uncertainties": [item.to_dict() for item in related_uncertainties],
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


def _effective_instructions(
    services: AppServices,
    *,
    workspace: str,
    target_path: str,
    materialization_owners: list[str] | None,
    selected_source_ids: list[str] | None,
    materialization_revisions: list[str] | None,
    expected_materialization_revisions: list[str] | None,
    expected_discovery_digest: str | None = None,
) -> instructions_api.EffectiveInstructionsProjectionV1:
    try:
        return services.impact_projection_service.effective_instructions(
            workspace=workspace,
            target_path=target_path,
            selected_materialization_owners=materialization_owners,
            selected_source_ids=selected_source_ids or (),
            materialization_revisions=_revision_bindings(
                materialization_revisions,
                field_name="materialization_revision",
            ),
            expected_materialization_revisions=_revision_bindings(
                expected_materialization_revisions,
                field_name="expected_materialization_revision",
            ),
            expected_discovery_digest=expected_discovery_digest,
        )
    except StaleEffectiveInstructionsProjectionError as exc:
        raise HTTPException(
            status_code=409,
            detail=_detail("resnapshot_required", str(exc)),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_instruction_request", str(exc)),
        ) from exc


def _revision_bindings(
    values: list[str] | None,
    *,
    field_name: str,
) -> dict[str, str]:
    if values is None:
        return {}
    if len(values) > MAX_INSTRUCTION_REVISION_BINDINGS:
        raise ValueError(
            f"{field_name} exceeds {MAX_INSTRUCTION_REVISION_BINDINGS} items"
        )
    revisions: dict[str, str] = {}
    for value in values:
        owner, separator, revision = value.partition("=")
        if (
            separator != "="
            or not owner
            or not revision
            or len(owner) > 256
            or len(revision) > 256
            or owner in revisions
        ):
            raise ValueError(f"{field_name} must contain unique owner=revision items")
        revisions[owner] = revision
    return revisions


def _instruction_projection_summary(
    projection: instructions_api.EffectiveInstructionsProjectionV1,
) -> dict[str, object]:
    included_count = sum(
        item.disposition.value == "include" for item in projection.sources
    )
    return {
        "format": projection.format,
        "source_revision": projection.source_revision,
        "discovery_digest": projection.discovery_digest,
        "config_digest": projection.config_digest,
        "target_path": projection.target_path,
        "source_count": len(projection.sources),
        "included_count": included_count,
        "omitted_count": len(projection.sources) - included_count,
        "conflict_count": len(projection.conflicts),
        "uncertainty_count": len(projection.uncertainties),
        "token_summary": projection.lens.token_summary.to_dict(),
        "is_partial": projection.lens.is_partial,
        "launch_ready": projection.launch_ready,
        "read_only": projection.read_only,
        "auto_materialized": projection.auto_materialized,
    }


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


__all__ = [
    "MAX_IMPACT_CHANGED_PATHS",
    "MAX_INSTRUCTION_PAGE_SIZE",
    "MAX_INSTRUCTION_REVISION_BINDINGS",
    "create_router",
]
