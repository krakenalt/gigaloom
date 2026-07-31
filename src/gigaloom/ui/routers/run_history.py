"""Run History domain routes."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from gigaloom.execution import ExecutionTransport
from gigaloom.pr_artifacts import (
    build_pr_artifact,
    pr_artifact_to_dict,
)
from gigaloom.provenance import build_replay_request, run_provenance_to_dict
from gigaloom.provider_account_sessions import ProviderAccountSessionError
from gigaloom.sessions import (
    RunNotFoundError,
    SessionNotFoundError,
)
from gigaloom.sessions.models import (
    HarnessRun,
    bundle_to_dict,
    run_to_dict,
    session_to_dict,
)
from gigaloom.sessions.store import title_from_prompt
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.routers.run_actions import validate_run_action_binding
from gigaloom.ui.services.navigation import session_summary as _session_summary
from gigaloom.ui.services.provenance import (
    _build_current_run_provenance,
    _latest_raw_request_for_run,
    _reviewed_evidence_for_run,
)
from gigaloom.ui.services.run_execution import _fork_session_from_run
from gigaloom.worktrees import (
    run_diff_response,
)


def create_router(services: AppServices) -> APIRouter:
    """Create the run history router."""
    router = ContractAPIRouter()

    def _run_provenance_response(run: HarnessRun) -> dict[str, Any]:
        provenance = _build_current_run_provenance(
            store=services.session_store,
            registry=services.registry,
            config=services.config,
            run=run,
            runtime_store=services.runtime_store,
        )
        return {
            "run": run_to_dict(run),
            "provenance": run_provenance_to_dict(provenance),
        }

    @router.db_read.get("/api/runs/{run_id}/diff")
    def run_diff(run_id: str) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return {"run": run_to_dict(run), "diff": run_diff_response(run.metadata)}

    @router.db_read.get("/api/runs/{run_id}/pr")
    def run_pr_artifact(run_id: str) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        artifact = build_pr_artifact(run)
        return {"run": run_to_dict(run), "pr_artifact": pr_artifact_to_dict(artifact)}

    @router.db_read.get("/api/runs/{run_id}/provenance")
    def run_provenance(run_id: str) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return _run_provenance_response(run)

    @router.bounded_job.post("/api/runs/{run_id}/replay")
    def replay_run(
        run_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
            raw_request = _latest_raw_request_for_run(services.session_store, run)
            replay_payload = build_replay_request(
                run,
                raw_request=raw_request,
                reviewed_evidence=_reviewed_evidence_for_run(
                    services.runtime_store, run.id
                ),
            )
            if "stream" in payload:
                replay_payload["stream"] = bool(payload.get("stream"))
            if (
                replay_payload.get("execution_transport")
                == ExecutionTransport.NATIVE_STRUCTURED.value
            ):
                if services.job_dispatcher is None:
                    raise ValueError(
                        "native_structured replay requires the durable runtime"
                    )
                if run.mode == "edit":
                    replay_payload["workspace_policy"] = "worktree"
                replay_session = services.session_runner.create_session(
                    title=f"Replay: {title_from_prompt(run.prompt)}",
                    workspace=run.workspace,
                    default_harness_id=run.harness_id,
                    default_model=run.model,
                    default_api_mode=run.api_mode,
                    default_mode=run.mode,
                )
                replay_extra = replay_payload.get("extra")
                replay_source_value = (
                    replay_extra.get("replay_source")
                    if isinstance(replay_extra, Mapping)
                    else None
                )
                replay_source = (
                    dict(replay_source_value)
                    if isinstance(replay_source_value, Mapping)
                    else {}
                )
                replay_session = services.session_store.update_session(
                    replay_session.id,
                    metadata={
                        **dict(replay_session.metadata),
                        "replay_source": replay_source,
                    },
                )
                submission = services.job_dispatcher.submit(
                    replay_session.id,
                    replay_payload,
                    idempotency_key=f"replay:{run.id}:{replay_session.id}",
                    origin="manual",
                )
                return {
                    "session": session_to_dict(replay_session),
                    "run": run_to_dict(submission.queued.run),
                    "source_run": run_to_dict(run),
                    "replay_request": replay_payload,
                    "replay": {
                        "source": replay_source,
                        "destination_harness_session_id": replay_session.id,
                        "provider_session_pending": True,
                    },
                }
            result = services.session_runner.run_in_session(
                run.session_id, replay_payload
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response = result.to_dict()
        response["source_run"] = run_to_dict(run)
        response["replay_request"] = replay_payload
        return response

    @router.bounded_job.post("/api/runs/{run_id}/fork")
    def fork_run(
        run_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
            validate_run_action_binding(run, payload)
            session = _fork_session_from_run(services.session_store, run)
            bundle = services.legacy_bundle_compatibility.export_session_bundle(
                session.id
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "source_run": run_to_dict(run),
            "session": _session_summary(services.session_store, session.id),
            "bundle": bundle_to_dict(bundle),
        }

    @router.db_read.get("/api/runs/{run_id}/patch")
    def run_patch(run_id: str) -> Response:
        try:
            run = services.session_store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        artifact = build_pr_artifact(run)
        return Response(content=artifact.patch, media_type="text/plain")

    return router
