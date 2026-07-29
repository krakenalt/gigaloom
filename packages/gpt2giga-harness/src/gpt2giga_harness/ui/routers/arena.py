"""Arena domain routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse

from gpt2giga_harness.arena import (
    ArenaNotFoundError,
    ArenaReviewConflictError,
    HarnessArenaChildRun,
    arena_has_verdict,
    continue_arena,
    queue_arena,
    queue_arena_follow_up,
    record_arena_verdict,
    run_arena,
)
from gpt2giga_harness.attachments import (
    AttachmentValidationError,
)
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.project import resolve_project
from gpt2giga_harness.provenance import build_replay_request
from gpt2giga_harness.sessions import (
    RunNotFoundError,
    SessionNotFoundError,
)
from gpt2giga_harness.sessions.store import title_from_prompt
from gpt2giga_harness.ui.async_execution import (
    ConformantAPIRoute,
    run_in_threadpool,
    run_stream_offload,
)
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.services.arena import arena_response as _arena_response
from gpt2giga_harness.ui.services.arena import (
    arena_summary_response as _arena_summary_response,
)
from gpt2giga_harness.ui.services.arena import (
    bounded_arena_workspace_paths as _bounded_arena_workspace_paths,
)
from gpt2giga_harness.ui.services.arena import first_text as _first_text
from gpt2giga_harness.ui.services.attachments import (
    attachment_limits as _attachment_limits,
)
from gpt2giga_harness.ui.services.attachments import (
    attachment_workspace as _attachment_workspace,
)
from gpt2giga_harness.ui.services.attachments import (
    session_project_id as _session_project_id,
)
from gpt2giga_harness.ui.services.provenance import (
    _latest_raw_request_for_run,
    _reviewed_evidence_for_run,
)
from gpt2giga_harness.ui.services.request_values import optional_text as _optional_text
from gpt2giga_harness.ui.streaming.events import arena_events as _arena_events
from gpt2giga_harness.ui.streaming.events import arena_sse_event as _arena_sse_event
from gpt2giga_harness.ui.streaming.events import (
    arena_status_is_terminal as _arena_status_is_terminal,
)
from gpt2giga_harness.workspace import (
    resolve_workspace,
)


def create_router(services: AppServices) -> APIRouter:
    """Create the arena router."""
    router = APIRouter(route_class=ConformantAPIRoute)

    @router.get("/api/arena/runs")
    def list_arena_runs(
        workspace: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        resolved_workspace = resolve_workspace(_optional_text(workspace))
        arenas = services.arena_store.list(workspace=resolved_workspace, limit=limit)
        return {"arenas": [_arena_summary_response(arena) for arena in arenas]}

    @router.post("/api/arena/runs")
    async def create_arena_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            payload = dict(payload)
            if not str(payload.get("prompt") or "").strip():
                raise ValueError("prompt is required")
            if _first_text(payload.get("harness_ids")) is None:
                raise ValueError("harness_ids must contain at least one harness")
            workspace_paths = _bounded_arena_workspace_paths(
                payload.pop("workspace_paths", None)
            )
            session_id = _optional_text(payload.get("session_id"))
            if workspace_paths and session_id is None:
                session = services.session_runner.create_session(
                    title=title_from_prompt(str(payload.get("prompt") or "")),
                    workspace=_optional_text(payload.get("workspace")),
                    default_harness_id=_first_text(payload.get("harness_ids"))
                    or "echo",
                    default_model=_optional_text(payload.get("model")),
                    default_api_mode=payload.get("api_mode"),
                    default_mode=str(payload.get("mode") or "plan"),
                )
                session_id = session.id
                payload["session_id"] = session_id
            if workspace_paths:
                session = services.session_store.get_session(session_id or "")
                workspace_root = _attachment_workspace(session, payload)
                attachment_ids = [
                    services.attachment_store.create_workspace_reference(
                        session_id=session.id,
                        project_id=_session_project_id(session)
                        or resolve_project(
                            workspace_root, data_dir=services.config.data_dir
                        ).id,
                        workspace_root=workspace_root,
                        path=path,
                        metadata={"arena_shared": True},
                        limits=_attachment_limits(
                            session, workspace_root=workspace_root
                        ),
                    ).id
                    for path in workspace_paths
                ]
                payload["attachment_ids"] = attachment_ids
            arena_runner = (
                queue_arena if services.job_dispatcher is not None else run_arena
            )
            arena = await run_in_threadpool(
                arena_runner,
                runner=services.session_runner,
                arena_store=services.arena_store,
                payload=payload,
                session_id=_optional_text(payload.get("session_id")),
                **{"dispatcher": services.job_dispatcher}
                if services.job_dispatcher is not None
                else {},
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await run_in_threadpool(_arena_response, arena, services.session_store)

    @router.get("/api/arena/runs/{arena_id}")
    def get_arena_run(arena_id: str) -> dict[str, Any]:
        try:
            arena = services.arena_store.get(arena_id)
        except ArenaNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Arena run not found") from exc
        return _arena_response(arena, services.session_store)

    @router.post("/api/arena/runs/{arena_id}/turns")
    async def create_arena_follow_up(
        arena_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            arena = await run_in_threadpool(services.arena_store.get, arena_id)
            if arena_has_verdict(arena):
                raise ArenaReviewConflictError(
                    "arena verdict is immutable; start a new comparison"
                )
            payload = dict(payload)
            if not str(payload.get("prompt") or "").strip():
                raise ValueError("prompt is required")
            workspace_paths = _bounded_arena_workspace_paths(
                payload.pop("workspace_paths", None)
            )
            if workspace_paths:
                session = await run_in_threadpool(
                    services.session_store.get_session, arena.session_id
                )
                workspace_root = _attachment_workspace(session, payload)

                def create_shared_attachments() -> list[str]:
                    return [
                        services.attachment_store.create_workspace_reference(
                            session_id=session.id,
                            project_id=_session_project_id(session)
                            or resolve_project(
                                workspace_root, data_dir=services.config.data_dir
                            ).id,
                            workspace_root=workspace_root,
                            path=path,
                            metadata={"arena_shared": True, "arena_id": arena.id},
                            limits=_attachment_limits(
                                session, workspace_root=workspace_root
                            ),
                        ).id
                        for path in workspace_paths
                    ]

                payload["attachment_ids"] = await run_in_threadpool(
                    create_shared_attachments
                )
            follow_up_runner = (
                queue_arena_follow_up
                if services.job_dispatcher is not None
                else continue_arena
            )
            arena = await run_in_threadpool(
                follow_up_runner,
                runner=services.session_runner,
                arena_store=services.arena_store,
                arena=arena,
                payload=payload,
                **{"dispatcher": services.job_dispatcher}
                if services.job_dispatcher is not None
                else {},
            )
        except ArenaNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Arena run not found") from exc
        except ArenaReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (AttachmentValidationError, SessionNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await run_in_threadpool(_arena_response, arena, services.session_store)

    @router.post("/api/arena/runs/{arena_id}/verdict")
    def create_arena_verdict(
        arena_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            arena = services.arena_store.get(arena_id)
            arena = record_arena_verdict(
                arena_store=services.arena_store,
                session_store=services.session_store,
                arena=arena,
                payload=payload,
            )
        except ArenaNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Arena run not found") from exc
        except ArenaReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _arena_response(arena, services.session_store)

    @router.post("/api/arena/runs/{arena_id}/children/{child_index}/retry")
    async def retry_arena_child(arena_id: str, child_index: int) -> dict[str, Any]:
        try:
            arena = await run_in_threadpool(services.arena_store.get, arena_id)
            if arena_has_verdict(arena):
                raise ArenaReviewConflictError(
                    "arena verdict is immutable; start a new comparison"
                )
            child = next(
                (item for item in arena.child_runs if item.index == child_index)
            )
            if child.run_id is None or child.session_id is None:
                raise ValueError("arena child has not started")
            source_run = await run_in_threadpool(
                services.session_store.get_run, child.run_id
            )
            raw_request = await run_in_threadpool(
                _latest_raw_request_for_run, services.session_store, source_run
            )
            replay_payload = build_replay_request(
                source_run,
                raw_request=raw_request,
                reviewed_evidence=_reviewed_evidence_for_run(
                    services.runtime_store, source_run.id
                ),
            )
            if source_run.status.value in {"queued", "running", "retry_wait"}:
                raise ValueError("arena child is still active")
            replay_payload["extra"] = {
                **dict(replay_payload.get("extra") or {}),
                "arena": {
                    "arena_id": arena.id,
                    "child_index": child.index,
                    "child_count": len(arena.harness_ids),
                    "parent_session_id": arena.session_id,
                    "turn_index": max(int(arena.metadata.get("turn_count") or 0), 0),
                },
            }
            target_session_id = child.session_id
            if (
                replay_payload.get("execution_transport")
                == ExecutionTransport.NATIVE_STRUCTURED.value
            ):
                if services.job_dispatcher is None:
                    raise ValueError(
                        "native_structured Arena retry requires the durable runtime"
                    )
                if source_run.mode == "edit":
                    replay_payload["workspace_policy"] = "worktree"
                retry_session = services.session_runner.create_session(
                    title=f"Arena retry: {title_from_prompt(source_run.prompt)}",
                    workspace=source_run.workspace,
                    default_harness_id=source_run.harness_id,
                    default_model=source_run.model,
                    default_api_mode=source_run.api_mode,
                    default_mode=source_run.mode,
                )
                retry_session = services.session_store.update_session(
                    retry_session.id,
                    metadata={
                        **dict(retry_session.metadata),
                        "arena_retry_source_run_id": source_run.id,
                        "arena_id": arena.id,
                        "arena_child_index": child.index,
                    },
                )
                target_session_id = retry_session.id
            if services.job_dispatcher is not None:
                submission = await run_in_threadpool(
                    services.job_dispatcher.submit,
                    target_session_id,
                    replay_payload,
                    idempotency_key=f"arena:{arena.id}:{child.index}:retry:{source_run.id}",
                    origin="manual",
                )
                replacement = HarnessArenaChildRun(
                    harness_id=child.harness_id,
                    index=child.index,
                    session_id=target_session_id,
                    run_id=submission.queued.run.id,
                    status="queued",
                )
            else:
                result = await run_in_threadpool(
                    services.session_runner.run_in_session,
                    target_session_id,
                    replay_payload,
                )
                replacement = HarnessArenaChildRun(
                    harness_id=child.harness_id,
                    index=child.index,
                    session_id=target_session_id,
                    run_id=result.run.id,
                    status=result.run.status.value,
                    error=result.run.error,
                    result_text=result.result.text
                    if result.run.status.value == "succeeded"
                    else None,
                )
            arena = services.arena_store.upsert_child(arena.id, replacement)
        except ArenaNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Arena run not found") from exc
        except ArenaReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RunNotFoundError, StopIteration) as exc:
            raise HTTPException(
                status_code=404, detail="Arena child not found"
            ) from exc
        except (SessionNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await run_in_threadpool(_arena_response, arena, services.session_store)

    @router.get("/api/arena/runs/{arena_id}/events/stream")
    async def arena_events_stream(
        arena_id: str, after_id: str | None = Query(default=None)
    ) -> StreamingResponse:
        try:
            await run_stream_offload(services.arena_store.get, arena_id)
        except ArenaNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Arena run not found") from exc

        async def stream_events():
            last_id = _optional_text(after_id)

            def poll_stream(cursor_value: str | None):
                current_arena = services.arena_store.get(arena_id)
                events = _arena_events(
                    current_arena, services.session_store, after_id=cursor_value
                )
                return (current_arena, events)

            while True:
                try:
                    current_arena, events = await run_stream_offload(
                        poll_stream, last_id
                    )
                except ArenaNotFoundError:
                    break
                for child, event in events:
                    last_id = event.id
                    yield _arena_sse_event(current_arena, child, event)
                if _arena_status_is_terminal(current_arena.status) and (not events):
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
