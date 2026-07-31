"""Reviewed Arena inspection and manual winner handoff routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from gigaloom.review.arena.api import (
    ReviewWinnerHandoff,
    WinnerReviewCommand,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.operator_arena import (
    ReviewWinnerCommand,
    ReviewedArenaConflictError,
    ReviewedArenaProjection,
)
from gigaloom.ui.services.operator_workspace import operator_scope


class ReviewWinnerPayload(BaseModel):
    """Strict optimistic request for the only admitted Arena action."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    arbitration_receipt_sha256: str
    candidate_evidence_sha256: str
    idempotency_key: str


def create_router(services: AppServices) -> APIRouter:
    """Create the owner-backed Reviewed Arena operator routes."""
    router = ContractAPIRouter()

    @router.db_read.get("/api/operator/arenas/{arena_id}/reviewed")
    def reviewed_arena(
        arena_id: str,
        request: Request,
        workspace_id: str = Query(...),
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, workspace_id)
        owner = services.reviewed_arena_owner
        if owner is None:
            raise HTTPException(
                status_code=503,
                detail=_detail(
                    "reviewed_arena_unavailable",
                    "Reviewed Arena owner is unavailable",
                ),
            )
        try:
            projection = owner.get_reviewed_arena(
                arena_id=arena_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            checked = ReviewedArenaProjection(
                arena_id=projection.arena_id,
                owner_id=projection.owner_id,
                workspace_id=projection.workspace_id,
                candidates=projection.candidates,
                eligibility=projection.eligibility,
                arbitration=projection.arbitration,
                reviewer_verdict=projection.reviewer_verdict,
                handoff=projection.handoff,
                allowed_commands=projection.allowed_commands,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("reviewed_arena_not_found", "Arena was not found"),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("reviewed_arena_forbidden", "Arena is forbidden"),
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "reviewed_arena_contract_invalid",
                    "Reviewed Arena owner returned an invalid projection",
                ),
            ) from exc
        if (
            checked.arena_id != arena_id
            or checked.owner_id != owner_id
            or checked.workspace_id != workspace_id
        ):
            raise HTTPException(
                status_code=403,
                detail=_detail(
                    "reviewed_arena_binding_mismatch",
                    "Reviewed Arena binding does not match",
                ),
            )
        return {"arena": checked.to_dict()}

    @router.db_atomic.post("/api/operator/arenas/{arena_id}/review-winner")
    def review_winner(
        arena_id: str,
        payload: ReviewWinnerPayload,
        request: Request,
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, payload.workspace_id)
        owner = services.reviewed_arena_owner
        if owner is None:
            raise HTTPException(
                status_code=503,
                detail=_detail(
                    "reviewed_arena_unavailable",
                    "Reviewed Arena owner is unavailable",
                ),
            )
        try:
            command = ReviewWinnerCommand(
                arena_id=arena_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                arbitration_receipt_sha256=(payload.arbitration_receipt_sha256),
                candidate_evidence_sha256=payload.candidate_evidence_sha256,
                idempotency_key=payload.idempotency_key,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=_detail(
                    "reviewed_arena_command_invalid",
                    "Review winner command is invalid",
                ),
            ) from exc
        try:
            returned = owner.review_winner(command)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("reviewed_arena_not_found", "Arena was not found"),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("reviewed_arena_forbidden", "Arena is forbidden"),
            ) from exc
        except ReviewedArenaConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "reviewed_arena_stale",
                    "Arena evidence changed; resnapshot required",
                ),
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "reviewed_arena_stale",
                    "Arena evidence changed; resnapshot required",
                ),
            ) from exc
        try:
            result = ReviewWinnerHandoff.from_dict(returned.to_dict())
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "reviewed_arena_handoff_invalid",
                    "Review owner returned an invalid handoff",
                ),
            ) from exc
        if (
            result.arena_id != command.arena_id
            or result.owner_id != command.owner_id
            or result.workspace_id != command.workspace_id
            or result.arbitration_receipt_sha256 != command.arbitration_receipt_sha256
            or result.candidate_evidence_sha256 != command.candidate_evidence_sha256
            or result.allowed_command is not WinnerReviewCommand.REVIEW_WINNER
            or result.automatic_apply
        ):
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "reviewed_arena_handoff_invalid",
                    "Review owner returned an invalid handoff",
                ),
            )
        return {"handoff": result.to_dict()}

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
