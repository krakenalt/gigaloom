"""Domain router extracted from the FastAPI composition root."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from gigaloom.native.process import (
    NativeProcessNotFoundError,
    NativeProcessStatus,
    native_output_chunk_to_dict,
    native_process_ref_to_dict,
)
from gigaloom.sessions.models import (
    HarnessMessage,
    event_to_dict,
    message_to_dict,
    run_to_dict,
)
from gigaloom.sessions.redaction import redact_for_storage
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.ui.async_execution import (
    ContractAPIRouter,
    run_in_threadpool,
    run_stream_offload,
)
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.native_process_sync import (
    _native_run_events,
    _native_run_messages,
    _sync_native_process_run,
)
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.streaming.events import (
    native_output_sse as _native_output_sse,
)
from gigaloom.ui.streaming.events import (
    native_sse_cursor as _native_sse_cursor,
)

NATIVE_SUBMIT_KEY_DELAY_SECONDS = 0.05
NATIVE_OUTPUT_STREAM_HEARTBEAT_SECONDS = 15.0


def create_router(services: AppServices) -> APIRouter:
    """Create the native domain router with typed application services."""
    router = ContractAPIRouter()

    @router.proc_async_atomic.post("/api/native/processes/{process_id}/input")
    async def native_process_input(
        process_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        message = None
        try:
            data = payload.get("data", payload.get("text", ""))
            process_ref = await run_in_threadpool(
                services.native_process_manager.write, process_id, str(data)
            )
            if payload.get("submit") is True:
                await asyncio.sleep(NATIVE_SUBMIT_KEY_DELAY_SECONDS)
                process_ref = await run_in_threadpool(
                    services.native_process_manager.write, process_id, "\r"
                )
            run = await run_in_threadpool(
                _sync_native_process_run,
                services.session_store,
                process_ref,
                native_registry=services.native_registry,
                native_index_store=services.native_index_store,
            )
            message_content = _optional_text(payload.get("message"))
            if message_content is not None and run is not None:
                message = await run_in_threadpool(
                    services.session_store.append_message,
                    HarnessMessage(
                        id=new_id("msg"),
                        session_id=run.session_id,
                        run_id=run.id,
                        role="user",
                        content=str(redact_for_storage(message_content)),
                        created_at=utc_now(),
                        harness_id=run.harness_id,
                        model=run.model,
                        api_mode=run.api_mode,
                        metadata={"source": "native_stdin"},
                    ),
                )
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "process": native_process_ref_to_dict(process_ref),
            "run": run_to_dict(run) if run is not None else None,
            "message": message_to_dict(message) if message is not None else None,
        }

    @router.proc_read.get("/api/native/processes/{process_id}/output")
    def native_process_output(
        process_id: str, cursor: int = Query(default=0, ge=0)
    ) -> dict[str, Any]:
        try:
            chunk = services.native_process_manager.read_since(process_id, cursor)
            process_ref = services.native_process_manager.status(process_id)
            run = _sync_native_process_run(
                services.session_store,
                process_ref,
                native_registry=services.native_registry,
                native_index_store=services.native_index_store,
            )
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc
        payload = native_output_chunk_to_dict(chunk)
        payload["run"] = run_to_dict(run) if run is not None else None
        payload["messages"] = (
            [
                message_to_dict(message)
                for message in _native_run_messages(services.session_store, run)
            ]
            if run is not None
            else []
        )
        payload["events"] = (
            [
                event_to_dict(event)
                for event in _native_run_events(services.session_store, run)
            ]
            if run is not None
            else []
        )
        return payload

    @router.stream.get("/api/native/processes/{process_id}/output/stream")
    async def native_process_output_stream(
        process_id: str,
        cursor: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        stream_cursor = max(cursor, _native_sse_cursor(last_event_id))
        try:
            await run_stream_offload(services.native_process_manager.status, process_id)
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc

        async def stream_output():
            current_cursor = stream_cursor
            last_keepalive = asyncio.get_running_loop().time()

            def wait_stream(cursor_value: int):
                chunk, process_ref = services.native_process_manager.wait_for_output(
                    process_id,
                    cursor_value,
                    timeout_seconds=NATIVE_OUTPUT_STREAM_HEARTBEAT_SECONDS,
                )
                run = _sync_native_process_run(
                    services.session_store,
                    process_ref,
                    native_registry=services.native_registry,
                    native_index_store=services.native_index_store,
                )
                event_payload = native_output_chunk_to_dict(chunk)
                event_payload["run"] = run_to_dict(run) if run is not None else None
                event_payload["messages"] = (
                    [
                        message_to_dict(message)
                        for message in _native_run_messages(services.session_store, run)
                    ]
                    if run is not None
                    else []
                )
                event_payload["events"] = (
                    [
                        event_to_dict(event)
                        for event in _native_run_events(services.session_store, run)
                    ]
                    if run is not None
                    else []
                )
                return (chunk, process_ref, event_payload)

            while True:
                try:
                    chunk, process_ref, event_payload = await run_stream_offload(
                        wait_stream, current_cursor
                    )
                except NativeProcessNotFoundError:
                    break
                should_emit = (
                    bool(chunk.outputs)
                    or chunk.truncated
                    or process_ref.status is not NativeProcessStatus.RUNNING
                )
                if should_emit:
                    current_cursor = chunk.cursor
                    yield _native_output_sse(event_payload)
                    last_keepalive = asyncio.get_running_loop().time()
                if process_ref.status is not NativeProcessStatus.RUNNING:
                    break
                now = asyncio.get_running_loop().time()
                if now - last_keepalive >= NATIVE_OUTPUT_STREAM_HEARTBEAT_SECONDS:
                    yield ": keepalive\n\n"
                    last_keepalive = now

        return StreamingResponse(
            stream_output(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.proc.post("/api/native/processes/{process_id}/resize")
    def native_process_resize(
        process_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            process_ref = services.native_process_manager.resize(
                process_id, rows=payload.get("rows"), columns=payload.get("columns")
            )
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "process": native_process_ref_to_dict(process_ref),
            "rows": payload["rows"],
            "columns": payload["columns"],
        }

    @router.proc_read.get("/api/native/processes/{process_id}")
    def native_process_status(process_id: str) -> dict[str, Any]:
        try:
            process_ref = services.native_process_manager.status(process_id)
            run = _sync_native_process_run(
                services.session_store,
                process_ref,
                native_registry=services.native_registry,
                native_index_store=services.native_index_store,
            )
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc
        return {
            "process": native_process_ref_to_dict(process_ref),
            "run": run_to_dict(run) if run is not None else None,
        }

    @router.proc.delete("/api/native/processes/{process_id}")
    def native_process_stop(process_id: str) -> dict[str, Any]:
        try:
            process_ref = services.native_process_manager.stop(process_id)
            run = _sync_native_process_run(
                services.session_store,
                process_ref,
                native_registry=services.native_registry,
                native_index_store=services.native_index_store,
            )
        except NativeProcessNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="Native process not found"
            ) from exc
        return {
            "stopped": process_ref.status is not NativeProcessStatus.RUNNING,
            "cancel_requested": process_ref.cancel_requested_at is not None,
            "process": native_process_ref_to_dict(process_ref),
            "run": run_to_dict(run) if run is not None else None,
        }

    return router
