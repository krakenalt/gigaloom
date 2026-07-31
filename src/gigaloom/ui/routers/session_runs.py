"""Session Runs domain routes."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException, Query

from gigaloom.provider_account_sessions import ProviderAccountSessionError
from gigaloom.runtime.models import job_to_dict
from gigaloom.sessions import (
    SessionNotFoundError,
)
from gigaloom.sessions.models import (
    HarnessRun,
    run_to_dict,
)
from gigaloom.sessions.store import new_id
from gigaloom.ui.async_execution import ContractAPIRouter, run_in_threadpool
from gigaloom.ui.container import AppServices
from gigaloom.ui.services import ActiveHeadlessRun
from gigaloom.ui.services.navigation import session_summary as _session_summary
from gigaloom.ui.services.run_execution import _wait_for_started_run
from gigaloom.ui.services.session_queries import (
    events_after as _events_after,
    recent_runs as _recent_runs,
)
from gigaloom.ui.streaming.events import event_response as _event_response


def create_router(services: AppServices) -> APIRouter:
    """Create the session runs router."""
    router = ContractAPIRouter()

    async def _start_headless_run(
        session_id: str, payload: Mapping[str, Any]
    ) -> HarnessRun:
        if services.job_dispatcher is not None:
            idempotency_key = str(
                payload.get("idempotency_key") or f"ui_{new_id('submit')}"
            )
            submission = await run_in_threadpool(
                services.session_service.submit_turn,
                session_id,
                payload,
                idempotency_key=idempotency_key,
                origin="interactive",
            )
            return submission.queued.run
        existing_runs = await run_in_threadpool(
            _recent_runs,
            services.session_store,
            session_id,
            limit=100,
        )
        before_run_ids = {run.id for run in existing_runs}
        cancel_event = threading.Event()
        task = asyncio.create_task(
            run_in_threadpool(
                services.session_service.run_turn,
                session_id,
                payload,
                cancel_event=cancel_event,
            )
        )
        run = await _wait_for_started_run(
            store=services.session_store,
            session_id=session_id,
            before_run_ids=before_run_ids,
            task=task,
        )
        services.active_headless_runs[run.id] = ActiveHeadlessRun(
            task=task, cancel_event=cancel_event
        )
        task.add_done_callback(
            lambda _task, run_id=run.id: services.active_headless_runs.pop(run_id, None)
        )
        return run

    def _run_start_response(run: HarnessRun) -> dict[str, Any]:
        events = services.session_service.list_run_events(run.id)
        payload = {
            "session": _session_summary(services.session_store, run.session_id),
            "run": run_to_dict(run),
            "events": [_event_response(event) for event in events],
            "stream_url": f"/api/runs/{run.id}/events/stream",
            "cancel_url": f"/api/runs/{run.id}/cancel",
        }
        job = services.session_service.find_job_for_run(run.id)
        if job is not None:
            payload["job"] = job_to_dict(job)
        return payload

    @router.worker_client_key.post("/api/sessions/run/start")
    async def create_session_and_start_run(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            session = await run_in_threadpool(
                services.session_service.create_session,
                payload,
                title_from_turn=False,
                validate_harness=True,
            )
            run = await _start_headless_run(session.id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return await run_in_threadpool(_run_start_response, run)

    @router.worker_client_key.post("/api/sessions/{session_id}/run/start")
    async def start_run_in_session(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            await run_in_threadpool(services.session_store.get_session, session_id)
            run = await _start_headless_run(session_id, payload)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return await run_in_threadpool(_run_start_response, run)

    @router.bounded_job.post("/api/sessions/run")
    def create_session_and_run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        try:
            result = services.session_service.create_and_run(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @router.bounded_job.post("/api/sessions/{session_id}/run")
    def run_in_session(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            result = services.session_service.run_turn(session_id, payload)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @router.fs_read.get("/api/sessions/{session_id}/events")
    def session_events(
        session_id: str,
        run_id: str | None = Query(default=None),
        after_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            events = _events_after(
                services.session_store,
                session_id,
                run_id=run_id,
                after_id=after_id,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "events": [
                {
                    "id": event.id,
                    "session_id": event.session_id,
                    "run_id": event.run_id,
                    "type": event.type,
                    "message": event.message,
                    "payload": dict(event.payload),
                    "created_at": event.created_at,
                }
                for event in events
            ]
        }

    return router
