"""Same-origin managed terminal description and WebSocket attach routes."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
)
from starlette.websockets import WebSocketState

from gigaloom.native.terminal import (
    TerminalAccessDeniedError,
    TerminalAttachRejectedError,
    TerminalAttachRequest,
    TerminalConflictError,
    TerminalControlSession,
    TerminalNotFoundError,
    TerminalWebSocketCloseCode,
)
from gigaloom.ui.async_execution import ContractAPIRouter, run_stream_offload
from gigaloom.ui.container import AppServices
from gigaloom.ui.security import request_is_test_client
from gigaloom.ui.services.operator_terminal import TerminalBrowserProjection
from gigaloom.ui.services.operator_workspace import operator_scope


TERMINAL_SEND_TIMEOUT_SECONDS = 5.0


def create_router(services: AppServices) -> APIRouter:
    """Create the managed browser terminal route family."""
    router = ContractAPIRouter()

    @router.db_read.get("/api/operator/terminals/{terminal_id}/attach")
    def terminal_attach_description(
        terminal_id: str,
        request: Request,
        workspace_id: str = Query(...),
        session_id: str = Query(...),
        revision: int = Query(..., ge=1),
    ) -> dict[str, object]:
        owner_id, workspace_id = _scope(request, workspace_id)
        owner = services.terminal_browser_owner
        if owner is None:
            raise HTTPException(
                status_code=503,
                detail=_detail(
                    "terminal_attach_unavailable",
                    "Managed terminal browser owner is unavailable",
                ),
            )
        try:
            projection = owner.describe_terminal(
                terminal_id=terminal_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                session_id=session_id,
                revision=revision,
            )
            checked = TerminalBrowserProjection(
                record=projection.record,
                websocket_path=projection.websocket_path,
            )
        except TerminalNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=_detail("terminal_not_found", "Terminal was not found"),
            ) from exc
        except TerminalAccessDeniedError as exc:
            raise HTTPException(
                status_code=403,
                detail=_detail("terminal_forbidden", "Terminal is forbidden"),
            ) from exc
        except TerminalConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail=_detail(
                    "terminal_revision_changed",
                    "Terminal changed; resnapshot required",
                ),
            ) from exc
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=_detail(
                    "terminal_contract_invalid",
                    "Terminal owner returned an invalid projection",
                ),
            ) from exc
        record = checked.record
        if (
            record.id != terminal_id
            or record.identity.owner_id != owner_id
            or record.identity.workspace_id != workspace_id
            or record.identity.session_id != session_id
            or record.revision != revision
        ):
            raise HTTPException(
                status_code=403,
                detail=_detail(
                    "terminal_binding_mismatch",
                    "Terminal binding does not match",
                ),
            )
        return {"terminal": checked.to_dict()}

    @router.websocket("/api/operator/terminals/{terminal_id}/attach/ws")
    async def terminal_attach_socket(
        websocket: WebSocket,
        terminal_id: str,
        workspace_id: str = Query(...),
        session_id: str = Query(...),
        revision: int = Query(..., ge=1),
    ) -> None:
        owner = services.terminal_browser_owner
        if owner is None:
            raise WebSocketException(code=1013, reason="terminal_attach_unavailable")
        owner_id = _websocket_owner(websocket, services)
        origin = websocket.headers.get("origin")
        if origin is None:
            raise WebSocketException(
                code=int(TerminalWebSocketCloseCode.FORBIDDEN),
                reason="terminal_origin_forbidden",
            )
        try:
            session = owner.open_terminal(
                TerminalAttachRequest(
                    terminal_id=terminal_id,
                    owner_id=owner_id,
                    workspace_id=workspace_id,
                    session_id=session_id,
                    revision=revision,
                    origin=origin,
                )
            )
        except TerminalAttachRejectedError as exc:
            raise WebSocketException(
                code=int(exc.code),
                reason=exc.reason,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise WebSocketException(
                code=int(TerminalWebSocketCloseCode.PROTOCOL_ERROR),
                reason="terminal_attach_invalid",
            ) from exc
        await websocket.accept()
        await _relay_terminal(websocket, session)

    return router


async def _relay_terminal(
    websocket: WebSocket,
    session: TerminalControlSession,
) -> None:
    sender = asyncio.create_task(_send_terminal_output(websocket, session))
    receiver = asyncio.create_task(_receive_terminal_input(websocket, session))
    try:
        done, pending = await asyncio.wait(
            {sender, receiver},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            error = task.exception()
            if error is not None:
                session.close_with(
                    TerminalWebSocketCloseCode.INTERNAL_ERROR,
                    "terminal_bridge_failed",
                )
    finally:
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
        session.close()
        if websocket.application_state is WebSocketState.CONNECTED:
            with suppress(RuntimeError):
                await websocket.close(
                    code=int(
                        session.closed_code or TerminalWebSocketCloseCode.DETACHED
                    ),
                    reason=session.closed_reason or "terminal_detached",
                )


async def _send_terminal_output(
    websocket: WebSocket,
    session: TerminalControlSession,
) -> None:
    frame = session.next_frame()
    while session.closed_code is None:
        if frame is not None:
            try:
                await asyncio.wait_for(
                    websocket.send_bytes(frame),
                    timeout=TERMINAL_SEND_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                session.close_with(
                    TerminalWebSocketCloseCode.BACKPRESSURE,
                    "terminal_output_backpressure",
                )
                return
        frame = await run_stream_offload(session.pump_once)


async def _receive_terminal_input(
    websocket: WebSocket,
    session: TerminalControlSession,
) -> None:
    try:
        while session.closed_code is None:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            frame: bytes | str | None = message.get("bytes")
            if frame is None:
                frame = message.get("text")
            if frame is None:
                session.close_with(
                    TerminalWebSocketCloseCode.PROTOCOL_ERROR,
                    "terminal_protocol_error",
                )
                return
            try:
                await run_stream_offload(session.receive, frame)
            except TerminalAttachRejectedError as exc:
                session.close_with(exc.code, exc.reason)
                return
            except ValueError:
                session.close_with(
                    TerminalWebSocketCloseCode.PROTOCOL_ERROR,
                    "terminal_protocol_error",
                )
                return
    except WebSocketDisconnect:
        return


def _websocket_owner(websocket: WebSocket, services: AppServices) -> str:
    security = services.ui_security
    if not security.host_allowed(websocket) or not security.origin_allowed(websocket):
        raise WebSocketException(
            code=int(TerminalWebSocketCloseCode.FORBIDDEN),
            reason="terminal_origin_forbidden",
        )
    remote_session = security.remote_session(websocket)
    if security.local_mode:
        if not security.has_session(websocket) and not request_is_test_client(
            websocket
        ):
            raise WebSocketException(
                code=int(TerminalWebSocketCloseCode.FORBIDDEN),
                reason="terminal_session_required",
            )
        return "local_operator"
    if remote_session is None or remote_session.actor.role == "viewer":
        raise WebSocketException(
            code=int(TerminalWebSocketCloseCode.FORBIDDEN),
            reason="terminal_operator_required",
        )
    return remote_session.actor.actor_id


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
