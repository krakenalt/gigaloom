"""Bounded backend routes for the future MCP Apps Web iframe host."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request
from fastapi.responses import Response

from gigaloom.tools.mcp.apps import (
    MCPAppChannelError,
    MCPAppDisplayContext,
    MCPAppFallback,
    MCPAppFrameBinding,
    MCPAppFrameDescriptor,
    mcp_app_resource_headers,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.mcp_apps import (
    MCPAppFallbackResponse,
    MCPAppFrameCreateRequest,
    MCPAppFrameDescriptorResponse,
    MCPAppFrameResponse,
    MCPAppMessageResponse,
    MCPAppTeardownResponse,
)
from gigaloom.ui.services.mcp_apps import MCPAppHostOutcome, MCPAppHostService

MCPAppInstanceId = Annotated[str, Path(min_length=1, max_length=128)]
MCPAppSourceId = Annotated[
    str,
    Header(alias="X-GigaLoom-MCP-App-Source", min_length=1, max_length=128),
]


def create_router(service: MCPAppHostService) -> APIRouter:
    """Create MCP App routes without mutating the central router registry."""
    router = ContractAPIRouter()

    @router.fs_atomic.post(
        "/api/mcp-apps/frames",
        response_model=MCPAppFrameResponse,
    )
    def create_frame(payload: MCPAppFrameCreateRequest) -> MCPAppFrameResponse:
        outcome = service.create_frame(
            MCPAppFrameBinding(
                server_id=payload.server_id,
                tool_id=payload.tool_id,
                resource_sha256=payload.resource_sha256,
                workspace_id=payload.workspace_id,
                session_id=payload.session_id,
                run_id=payload.run_id,
            ),
            display=MCPAppDisplayContext(
                theme=payload.theme,
                locale=payload.locale,
                display_mode=payload.display_mode,
            ),
        )
        return _outcome_response(outcome)

    @router.fs_read.get(
        "/api/mcp-apps/frames/{instance_id}",
        response_model=MCPAppFrameDescriptorResponse,
    )
    def frame_descriptor(
        instance_id: MCPAppInstanceId,
    ) -> MCPAppFrameDescriptorResponse:
        try:
            return _descriptor_response(service.descriptor(instance_id))
        except MCPAppChannelError as exc:
            raise _http_error(exc) from exc

    @router.fs_read.get("/api/mcp-apps/frames/{instance_id}/resource")
    def frame_resource(instance_id: MCPAppInstanceId) -> Response:
        try:
            resource = service.resource(instance_id)
        except MCPAppChannelError as exc:
            raise _http_error(exc) from exc
        return Response(
            content=resource.html,
            headers={
                **mcp_app_resource_headers(),
                "Content-Type": resource.mime_type,
            },
        )

    @router.loop_atomic.post(
        "/api/mcp-apps/frames/{instance_id}/messages",
        response_model=MCPAppMessageResponse,
    )
    async def frame_message(
        instance_id: MCPAppInstanceId,
        request: Request,
        source_id: MCPAppSourceId,
    ) -> MCPAppMessageResponse:
        raw_message = await _bounded_body(
            request,
            limit=service.max_post_message_bytes,
        )
        try:
            accepted = service.accept_message(
                instance_id,
                source_id=source_id,
                raw_message=raw_message,
            )
        except MCPAppChannelError as exc:
            raise _http_error(exc) from exc
        return MCPAppMessageResponse(
            request_id=accepted.request_id,
            method=accepted.method,
        )

    @router.fs_atomic.delete(
        "/api/mcp-apps/frames/{instance_id}",
        response_model=MCPAppTeardownResponse,
    )
    def destroy_frame(instance_id: MCPAppInstanceId) -> MCPAppTeardownResponse:
        try:
            cancelled = service.teardown(instance_id)
        except MCPAppChannelError as exc:
            raise _http_error(exc) from exc
        return MCPAppTeardownResponse(cancelled_requests=len(cancelled))

    return router


async def _bounded_body(request: Request, *, limit: int) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid content length"
            ) from exc
        if declared < 0:
            raise HTTPException(status_code=400, detail="invalid content length")
        if declared > limit:
            raise HTTPException(status_code=413, detail="MCP App message is too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="MCP App message is too large")
    return bytes(body)


def _outcome_response(outcome: MCPAppHostOutcome) -> MCPAppFrameResponse:
    if outcome.descriptor is not None:
        return MCPAppFrameResponse(
            status="admitted",
            frame=_descriptor_response(outcome.descriptor),
        )
    assert outcome.fallback is not None
    return MCPAppFrameResponse(
        status="fallback",
        fallback=_fallback_response(outcome.fallback),
    )


def _descriptor_response(
    descriptor: MCPAppFrameDescriptor,
) -> MCPAppFrameDescriptorResponse:
    return MCPAppFrameDescriptorResponse(
        instance_id=descriptor.instance_id,
        server_id=descriptor.server_id,
        resource_sha256=descriptor.resource_sha256,
        resource_uri=descriptor.resource_uri,
        resource_url=f"/api/mcp-apps/frames/{descriptor.instance_id}/resource",
        sandbox=descriptor.sandbox,
        content_security_policy=descriptor.content_security_policy,
        channel_id=descriptor.channel_id,
        nonce=descriptor.nonce,
        source_id=descriptor.source_id,
        initialization=dict(descriptor.initialization),
    )


def _fallback_response(fallback: MCPAppFallback) -> MCPAppFallbackResponse:
    return MCPAppFallbackResponse(
        code=fallback.code.value,
        message=fallback.message,
        textual=fallback.textual,
        structured=dict(fallback.structured),
        denied_evidence=list(fallback.denied_evidence),
    )


def _http_error(exc: MCPAppChannelError) -> HTTPException:
    if exc.code in {"frame_not_found", "resource_unavailable"}:
        status_code = 404
    elif exc.code == "payload_limit":
        status_code = 413
    elif exc.code in {
        "replayed_request",
        "outstanding_limit",
        "request_history_limit",
        "frame_closed",
    }:
        status_code = 409
    elif exc.code in {
        "source_mismatch",
        "channel_mismatch",
        "nonce_mismatch",
        "method_denied",
    }:
        status_code = 403
    else:
        status_code = 400
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )
