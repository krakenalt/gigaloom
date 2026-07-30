"""Bounded Operator Evidence Workspace and Action Inbox APIs."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from gigaloom.review.workspace.api import EvidenceWorkspaceProjection
from gigaloom.runtime.action_inbox.api import (
    MAX_ACTION_INBOX_ITEMS,
    ActionInboxCommand,
    ActionInboxConflictError,
    ActionInboxForbiddenError,
    ActionInboxKind,
    ActionInboxNotFoundError,
    ActionInboxResponseRequest,
    ActionInboxResponseResult,
    ActionInboxValidationError,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.operator_workspace import (
    decode_inbox_cursor,
    encode_inbox_cursor,
    inbox_filter_digest,
    operator_scope,
)
from gigaloom.ui.streaming.operator_events import (
    operator_event_sse,
    operator_resnapshot_sse,
)


OPERATOR_EVENT_POLL_SECONDS = 0.25
OPERATOR_EVENT_HEARTBEAT_SECONDS = 15.0
MAX_INBOX_PAGE_SIZE = 100


class ActionInboxResponsePayload(BaseModel):
    """Strict optimistic response body for one Inbox item."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    expected_revision: str
    expected_item_sha256: str
    action: ActionInboxCommand
    idempotency_key: str
    response: dict[str, object] = Field(default_factory=dict)


def create_router(services: AppServices) -> APIRouter:
    """Create the cohesive Operator Workspace route family."""
    router = ContractAPIRouter()

    @router.db_read.get("/api/operator/runs/{run_id}/evidence")
    def run_evidence(
        run_id: str,
        request: Request,
        workspace_id: str = Query(...),
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, workspace_id)
        provider = services.operator_evidence_query
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail=_detail(
                    "evidence_unavailable",
                    "Evidence projection owner is unavailable",
                ),
            )
        try:
            projection = provider.get_evidence_workspace(
                run_id=run_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            checked = EvidenceWorkspaceProjection.from_dict(projection.to_dict())
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("evidence_not_found", "Run evidence was not found"),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("evidence_forbidden", "Run evidence is forbidden"),
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "evidence_contract_invalid",
                    "Evidence owner returned an invalid projection",
                ),
            ) from exc
        if (
            checked.run.run_id != run_id
            or checked.run.owner_id != owner_id
            or checked.run.workspace_id != workspace_id
        ):
            raise HTTPException(
                status_code=403,
                detail=_detail(
                    "evidence_binding_mismatch",
                    "Run evidence binding does not match",
                ),
            )
        return {"evidence": checked.to_dict()}

    @router.db_read.get("/api/operator/inbox")
    def inbox_page(
        request: Request,
        workspace_id: str = Query(...),
        limit: int = Query(default=50, ge=1, le=MAX_INBOX_PAGE_SIZE),
        cursor: str | None = Query(default=None),
        kind: str | None = Query(default=None),
        origin: str | None = Query(default=None),
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, workspace_id)
        kinds = _parse_kinds(kind)
        normalized_origin = _optional_token(origin, "origin")
        snapshot = services.action_inbox_service.snapshot(
            owner_id=owner_id,
            workspace_id=workspace_id,
            limit=MAX_ACTION_INBOX_ITEMS,
        )
        filtered = tuple(
            item
            for item in snapshot.items
            if (not kinds or item.kind.value in kinds)
            and (normalized_origin is None or item.origin == normalized_origin)
        )
        filter_sha256 = inbox_filter_digest(
            kinds=kinds,
            origin=normalized_origin,
        )
        try:
            offset = (
                decode_inbox_cursor(
                    cursor,
                    snapshot_sha256=snapshot.snapshot_sha256,
                    filter_sha256=filter_sha256,
                )
                if cursor is not None
                else 0
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "resnapshot_required",
                    "Inbox cursor is stale or invalid",
                ),
            ) from exc
        if offset > len(filtered):
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "resnapshot_required",
                    "Inbox cursor is outside the current snapshot",
                ),
            )
        page = filtered[offset : offset + limit]
        next_offset = offset + len(page)
        next_cursor = (
            encode_inbox_cursor(
                snapshot_sha256=snapshot.snapshot_sha256,
                filter_sha256=filter_sha256,
                offset=next_offset,
            )
            if next_offset < len(filtered)
            else None
        )
        return {
            "items": [item.to_dict() for item in page],
            "snapshot_sha256": snapshot.snapshot_sha256,
            "next_cursor": next_cursor,
            "has_more": next_cursor is not None,
            "resnapshot_required": False,
        }

    @router.db_atomic.post("/api/operator/inbox/{authority}/{item_id}/responses")
    def respond_to_inbox(
        authority: str,
        item_id: str,
        payload: ActionInboxResponsePayload,
        request: Request,
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, payload.workspace_id)
        try:
            result = services.action_inbox_service.respond(
                ActionInboxResponseRequest(
                    item_id=item_id,
                    authority=authority,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    expected_revision=payload.expected_revision,
                    expected_item_sha256=payload.expected_item_sha256,
                    action=payload.action,
                    idempotency_key=payload.idempotency_key,
                    response=payload.response,
                )
            )
        except ActionInboxNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("action_not_found", "Inbox action was not found"),
            ) from exc
        except ActionInboxForbiddenError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("action_forbidden", "Inbox action is forbidden"),
            ) from exc
        except ActionInboxConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "stale_action",
                    "Inbox action changed; resnapshot required",
                ),
            ) from exc
        except ActionInboxValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=_detail("invalid_action", "Inbox action is invalid"),
            ) from exc
        if not result.idempotent_replay:
            services.operator_event_broker.publish(
                owner_id=owner_id,
                workspace_id=workspace_id,
                kind="inbox.changed",
                resource_id=result.item_id,
                revision=result.revision,
                sha256=result.receipt_sha256,
            )
        return {"result": _result_to_dict(result)}

    @router.stream.get("/api/operator/events")
    async def operator_events(
        request: Request,
        workspace_id: str = Query(...),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        owner_id, workspace_id = _scope(request, workspace_id)

        async def stream():
            cursor = last_event_id
            next_heartbeat = monotonic() + OPERATOR_EVENT_HEARTBEAT_SECONDS
            while True:
                page = services.operator_event_broker.read(
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    after=cursor,
                )
                if page.resnapshot_reason is not None:
                    yield operator_resnapshot_sse(page)
                    cursor = page.cursor
                else:
                    for event in page.events:
                        yield operator_event_sse(event)
                        cursor = event.cursor
                    if page.has_more:
                        continue
                if await request.is_disconnected():
                    break
                if monotonic() >= next_heartbeat:
                    yield ": heartbeat\n\n"
                    next_heartbeat = monotonic() + OPERATOR_EVENT_HEARTBEAT_SECONDS
                await asyncio.sleep(OPERATOR_EVENT_POLL_SECONDS)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


def _scope(request: Request, workspace_id: object) -> tuple[str, str]:
    try:
        return operator_scope(request, workspace_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_scope", "Operator scope is invalid"),
        ) from exc


def _parse_kinds(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    parsed = tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))
    try:
        for item in parsed:
            ActionInboxKind(item)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_filter", "Inbox kind filter is invalid"),
        ) from exc
    return parsed


def _optional_token(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or not normalized[0].islower()
        or not all(
            character.islower() or character.isdigit() or character in "._-"
            for character in normalized
        )
    ):
        raise HTTPException(
            status_code=422,
            detail=_detail("invalid_filter", f"Inbox {name} filter is invalid"),
        )
    return normalized


def _result_to_dict(result: ActionInboxResponseResult) -> dict[str, Any]:
    return {
        "response_id": result.response_id,
        "item_id": result.item_id,
        "authority": result.authority,
        "owner_id": result.owner_id,
        "workspace_id": result.workspace_id,
        "action": result.action.value,
        "status": result.status.value,
        "revision": result.revision,
        "receipt_sha256": result.receipt_sha256,
        "idempotent_replay": result.idempotent_replay,
    }


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
