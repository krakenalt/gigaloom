"""Run Streams domain routes."""

from __future__ import annotations

from time import monotonic
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from gpt2giga_harness.runtime.models import job_to_dict
from gpt2giga_harness.sessions import (
    RunNotFoundError,
    SessionNotFoundError,
)
from gpt2giga_harness.sessions.event_stream import StreamCapacityError, StreamSignal
from gpt2giga_harness.sessions.models import (
    HarnessStoredEvent,
    run_to_dict,
)
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.types import (
    HarnessEventType,
)
from gpt2giga_harness.ui.async_execution import (
    ConformantAPIRoute,
    run_stream_offload,
)
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.routers.tui_actions import validate_run_action_binding
from gpt2giga_harness.ui.services.request_values import optional_text as _optional_text
from gpt2giga_harness.ui.streaming.events import (
    encode_run_stream_cursor as _encode_run_stream_cursor,
)
from gpt2giga_harness.ui.streaming.events import (
    resolve_run_stream_cursor as _resolve_run_stream_cursor,
)
from gpt2giga_harness.ui.streaming.events import (
    run_resnapshot_sse as _run_resnapshot_sse,
)
from gpt2giga_harness.ui.streaming.events import run_sse_event as _run_sse_event
from gpt2giga_harness.ui.streaming.events import (
    run_status_is_terminal as _run_status_is_terminal,
)

RUN_EVENT_STREAM_HEARTBEAT_SECONDS = 15.0
RUN_EVENT_STREAM_POLL_SECONDS = 0.1


def create_router(services: AppServices) -> APIRouter:
    """Create the run streams router."""
    router = APIRouter(route_class=ConformantAPIRoute)

    @router.get("/api/runs/{run_id}/events/stream")
    async def run_events_stream(
        run_id: str,
        after_id: str | None = Query(default=None),
        tail_only: bool = Query(default=False),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            initial_run = await run_stream_offload(
                services.session_service.get_run, run_id
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        try:
            subscription = services.run_event_broker.subscribe(run_id)
        except StreamCapacityError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            cursor_position = await run_stream_offload(
                _resolve_run_stream_cursor,
                services.session_store,
                initial_run,
                _optional_text(last_event_id) or _optional_text(after_id),
                tail_only=tail_only,
            )
        except ValueError as exc:
            subscription.close()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def stream_events():
            current_offset = cursor_position.offset
            terminal_event_seen = cursor_position.terminal_seen
            next_heartbeat_at = monotonic() + RUN_EVENT_STREAM_HEARTBEAT_SECONDS
            try:
                while True:
                    try:
                        current_run, page = await run_stream_offload(
                            services.session_service.read_run_event_tail,
                            run_id,
                            current_offset,
                        )
                    except (RunNotFoundError, SessionNotFoundError, ValueError):
                        break
                    for item in page.items:
                        current_offset = item.next_offset
                        event = item.event
                        if event.type == HarnessEventType.RUN_FINISHED.value:
                            terminal_event_seen = True
                        cursor = _encode_run_stream_cursor(
                            current_run,
                            current_offset,
                            terminal_event_seen=terminal_event_seen,
                        )
                        yield _run_sse_event(event, cursor)
                    if page.next_offset > current_offset:
                        current_offset = page.next_offset
                    if page.has_more:
                        continue
                    if _run_status_is_terminal(current_run.status):
                        if terminal_event_seen:
                            break
                        terminal_event_seen = True
                        cursor = _encode_run_stream_cursor(
                            current_run, current_offset, terminal_event_seen=True
                        )
                        yield _run_sse_event(
                            HarnessStoredEvent(
                                id=f"evt_terminal_{current_run.id}",
                                session_id=current_run.session_id,
                                run_id=current_run.id,
                                type=HarnessEventType.RUN_FINISHED.value,
                                message="Harness run reached a terminal state.",
                                payload={
                                    "status": current_run.status,
                                    "synthetic": True,
                                },
                                created_at=current_run.finished_at
                                or current_run.updated_at
                                or current_run.created_at,
                            ),
                            cursor,
                        )
                        break
                    signal = await subscription.wait(RUN_EVENT_STREAM_POLL_SECONDS)
                    cursor = _encode_run_stream_cursor(
                        current_run,
                        current_offset,
                        terminal_event_seen=terminal_event_seen,
                    )
                    if signal is StreamSignal.RESNAPSHOT_REQUIRED:
                        yield _run_resnapshot_sse(current_run, cursor)
                    elif signal is None and monotonic() >= next_heartbeat_at:
                        yield ": heartbeat\n\n"
                        next_heartbeat_at = (
                            monotonic() + RUN_EVENT_STREAM_HEARTBEAT_SECONDS
                        )
            finally:
                subscription.close()

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/api/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        validate_run_action_binding(run, payload)
        if _run_status_is_terminal(run.status):
            return {"cancel_requested": False, "active": False, "run": run_to_dict(run)}
        durable_job = (
            services.runtime_store.find_job_for_run(run.id)
            if services.runtime_store is not None
            else None
        )
        if durable_job is not None:
            job = services.runtime_store.request_cancel(durable_job.id)
            attempts = services.runtime_store.list_attempts(job.id)
            active_attempt = next(
                (attempt for attempt in reversed(attempts) if attempt.run_id == run.id),
                None,
            )
            if active_attempt is None and job.status.value == "queued":
                job = services.runtime_store.transition_job(
                    job.id, "canceled", expected_status="queued"
                )
                run = services.session_store.update_run(
                    run.id,
                    status="canceled",
                    finished_at=utc_now(),
                    error="Harness run canceled before worker claim.",
                    metadata={**dict(run.metadata), "cancel_requested": True},
                )
            else:
                run = services.session_store.update_run(
                    run.id, metadata={**dict(run.metadata), "cancel_requested": True}
                )
            if not bool(run.metadata.get("cancel_event_recorded")):
                services.session_store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.CANCEL_REQUESTED.value,
                        message="Harness run cancellation requested.",
                        payload={
                            "job_id": job.id,
                            "active": active_attempt is not None,
                        },
                        created_at=utc_now(),
                        trace_id=job.id,
                        job_id=job.id,
                        attempt_id=active_attempt.id if active_attempt else None,
                    )
                )
            return {
                "cancel_requested": True,
                "active": active_attempt is not None,
                "job": job_to_dict(job),
                "run": run_to_dict(run),
            }
        active = services.active_headless_runs.get(run.id)
        if active is not None and active.task.done():
            active = None
        run = services.session_store.get_run(run.id)
        if _run_status_is_terminal(run.status):
            return {"cancel_requested": False, "active": False, "run": run_to_dict(run)}
        already_requested = bool(run.metadata.get("cancel_requested"))
        if active is not None:
            active.cancel_event.set()
            metadata = {**dict(run.metadata), "cancel_requested": True}
            run = services.session_store.update_run(run.id, metadata=metadata)
        else:
            metadata = {**dict(run.metadata), "cancel_requested": True}
            run = services.session_store.update_run(
                run.id,
                status="canceled",
                finished_at=utc_now(),
                error="Harness run canceled.",
                metadata=metadata,
            )
        if not already_requested:
            services.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type=HarnessEventType.CANCEL_REQUESTED.value,
                    message="Harness run cancellation requested.",
                    payload={"active": active is not None},
                    created_at=utc_now(),
                )
            )
            if active is None:
                services.session_store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.RUN_CANCELED.value,
                        message="Harness run canceled.",
                        payload={},
                        created_at=utc_now(),
                    )
                )
                services.session_store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.RUN_FINISHED.value,
                        message="Harness run finished.",
                        payload={"status": "canceled"},
                        created_at=utc_now(),
                    )
                )
        return {
            "cancel_requested": True,
            "active": active is not None,
            "run": run_to_dict(run),
        }

    return router
