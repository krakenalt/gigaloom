"""Routes extracted from the FastAPI composition root: evals."""

from __future__ import annotations

from typing import Any
from fastapi import Body, HTTPException, Query
from gigaloom.ui.services.arena import eval_run_response as _eval_run_response
from gigaloom.ui.services.attachments import text_tuple as _text_tuple
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.async_execution import ContractAPIRouter, run_in_threadpool
from gigaloom.evals import (
    EvalRunNotFoundError,
    EvalSpecNotFoundError,
    discover_eval_specs,
    eval_run_to_dict,
    eval_spec_load_error_to_dict,
    eval_spec_to_dict,
    load_eval_spec,
    queue_eval,
    run_eval,
)
from gigaloom.project import project_to_dict, resolve_project
from fastapi import APIRouter
from gigaloom.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.fs_read.get("/api/evals")
    def evals(workspace: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            project_context = resolve_project(
                _optional_text(workspace),
                data_dir=services.config.data_dir,
                load_config_name=False,
            )
            specs, errors = discover_eval_specs(project_context.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": project_to_dict(project_context),
            "specs": [eval_spec_to_dict(spec) for spec in specs],
            "errors": [eval_spec_load_error_to_dict(error) for error in errors],
            "runs": [
                eval_run_to_dict(eval_run)
                for eval_run in services.eval_store.list_runs(project_context)
            ],
        }

    @router.fs_read.get("/api/evals/runs/{eval_run_id}")
    def get_eval_run(eval_run_id: str) -> dict[str, Any]:
        try:
            eval_run = services.eval_store.get_any(eval_run_id)
        except EvalRunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Eval run not found") from exc
        return _eval_run_response(eval_run, services.session_store)

    @router.worker_job.post("/api/evals/{eval_name}/runs")
    async def create_eval_run(
        eval_name: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:

            def prepare_eval():
                project_context = resolve_project(
                    _optional_text(payload.get("workspace")),
                    data_dir=services.config.data_dir,
                    load_config_name=False,
                )
                return (
                    project_context,
                    load_eval_spec(project_context.root, eval_name),
                )

            project_context, spec = await run_in_threadpool(prepare_eval)
            eval_runner = (
                queue_eval if services.job_dispatcher is not None else run_eval
            )
            eval_run = await run_in_threadpool(
                eval_runner,
                runner=services.session_runner,
                eval_store=services.eval_store,
                project=project_context,
                spec=spec,
                harness_ids=_text_tuple(payload.get("harness_ids")),
                model=_optional_text(payload.get("model")),
                api_mode=payload.get("api_mode"),
                mode=_optional_text(payload.get("mode")),
                workspace_policy=_optional_text(payload.get("workspace_policy")),
                execution_transport=_optional_text(payload.get("execution_transport")),
                dry_run=bool(payload.get("dry_run")),
                repetitions=int(payload.get("repetitions") or 1),
                **{"dispatcher": services.job_dispatcher}
                if services.job_dispatcher is not None
                else {},
            )
        except EvalSpecNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Eval spec not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await run_in_threadpool(
            _eval_run_response, eval_run, services.session_store
        )

    return router
