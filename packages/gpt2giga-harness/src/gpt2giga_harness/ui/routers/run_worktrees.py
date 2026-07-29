"""Run Worktrees domain routes."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from gpt2giga_harness.pr_artifacts import (
    build_pr_artifact,
    create_pr_branch,
    pr_artifact_to_dict,
)
from gpt2giga_harness.runtime.policy import (
    REVIEWED_PROMOTION_APPLY_OWNER,
    REVIEWED_PROMOTION_BRANCH_OWNER,
    PermissionAction,
)
from gpt2giga_harness.sessions import (
    RunNotFoundError,
)
from gpt2giga_harness.sessions.models import (
    HarnessRun,
    HarnessStoredEvent,
    run_to_dict,
)
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.ui.async_execution import (
    ConformantAPIRoute,
)
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.services.request_values import optional_text as _optional_text
from gpt2giga_harness.worktrees import (
    WorktreeConflictError,
    WorktreeError,
    apply_run_diff,
    discard_run_worktree,
    open_worktree_response,
    review_run_diff,
    run_diff_response,
)


class ApprovalGate(Protocol):
    """Approval boundary required by reviewed worktree promotions."""

    def __call__(
        self,
        action: PermissionAction,
        run: HarnessRun,
        *,
        reason: str,
        preview: Mapping[str, Any],
        approval_binding: str | None = None,
        enforcement_owner: str | None = None,
    ) -> JSONResponse | None: ...


def create_router(services: AppServices, *, approval_gate: ApprovalGate) -> APIRouter:
    """Create the run worktrees router."""
    router = APIRouter(route_class=ConformantAPIRoute)

    @router.post("/api/runs/{run_id}/apply", response_model=None)
    def apply_run_patch(
        run_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any] | JSONResponse:
        try:
            run = services.session_store.get_run(run_id)
            branch_name = _optional_text(payload.get("branch_name"))
            review = review_run_diff(run.metadata, branch_name=branch_name)
            approval_response = approval_gate(
                PermissionAction.GIT_APPLY,
                run,
                reason="Apply an isolated worktree diff to the source checkout.",
                preview=review.to_preview(),
                approval_binding=review.approval_binding,
                enforcement_owner=REVIEWED_PROMOTION_APPLY_OWNER,
            )
            if approval_response is not None:
                return approval_response
            workspace_execution = apply_run_diff(
                run.metadata, review=review, branch_name=branch_name
            )
            metadata = {
                **dict(run.metadata),
                "workspace_execution": workspace_execution,
            }
            run = services.session_store.update_run(run.id, metadata=metadata)
            services.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="worktree_applied",
                    message="Applied isolated worktree diff to the source checkout.",
                    payload={
                        "changed_files": workspace_execution.get("changed_files", []),
                        "applied_branch": workspace_execution.get("applied_branch"),
                        "source_sha": review.source_sha,
                        "patch_sha256": review.patch_sha256,
                    },
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "applied": True,
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
        }

    @router.post("/api/runs/{run_id}/branch", response_model=None)
    def create_run_branch(
        run_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any] | JSONResponse:
        try:
            run = services.session_store.get_run(run_id)
            branch_name = (
                _optional_text(payload.get("branch_name"))
                or build_pr_artifact(run).branch_name_suggestion
            )
            review = review_run_diff(run.metadata, branch_name=branch_name)
            approval_response = approval_gate(
                PermissionAction.GIT_BRANCH_CREATE,
                run,
                reason="Create a local branch from the isolated run patch.",
                preview=review.to_preview(),
                approval_binding=review.approval_binding,
                enforcement_owner=REVIEWED_PROMOTION_BRANCH_OWNER,
            )
            if approval_response is not None:
                return approval_response
            branch = create_pr_branch(run, review=review, branch_name=branch_name)
            metadata = {
                **dict(run.metadata),
                "workspace_execution": branch["workspace_execution"],
            }
            run = services.session_store.update_run(run.id, metadata=metadata)
            artifact = build_pr_artifact(run)
            metadata = {
                **dict(run.metadata),
                "pr_artifact": pr_artifact_to_dict(artifact),
            }
            run = services.session_store.update_run(run.id, metadata=metadata)
            services.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="pr_branch_created",
                    message="Created local branch from run patch.",
                    payload={
                        "branch_name": branch["branch_name"],
                        "source_sha": review.source_sha,
                        "patch_sha256": review.patch_sha256,
                    },
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "branch_created": True,
            "branch_name": branch["branch_name"],
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
            "pr_artifact": pr_artifact_to_dict(build_pr_artifact(run)),
        }

    @router.post("/api/runs/{run_id}/discard")
    def discard_run_worktree_endpoint(run_id: str) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
            workspace_execution = discard_run_worktree(run.metadata)
            metadata = {
                **dict(run.metadata),
                "workspace_execution": workspace_execution,
            }
            run = services.session_store.update_run(run.id, metadata=metadata)
            services.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="worktree_discarded",
                    message="Discarded isolated worktree for this run.",
                    payload={"worktree_path": workspace_execution.get("worktree_path")},
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "discarded": True,
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
        }

    @router.post("/api/runs/{run_id}/open-worktree")
    def open_run_worktree(run_id: str) -> dict[str, Any]:
        try:
            run = services.session_store.get_run(run_id)
            response = open_worktree_response(run.metadata)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return {"run": run_to_dict(run), "worktree": response}

    return router
