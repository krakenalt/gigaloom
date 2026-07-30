"""Handoffs for the workflows subcontext."""

from __future__ import annotations

from typing import Any, Mapping
from gigaloom.projects.api import (
    RunDiffReview,
    WorktreeError,
    apply_run_diff,
    detect_overlapping_run_diffs,
    discard_run_worktree,
    prepare_run_diff_merge,
)
from .constants import WORKFLOW_COORDINATION_OUTPUT as WORKFLOW_COORDINATION_OUTPUT
from .coordinator import WorkflowCoordinator as WorkflowCoordinator
from .definitions import _mapping as _mapping, _optional_text as _optional_text
from .models import StepAttempt as StepAttempt, WorkflowRun as WorkflowRun


def workflow_coordination(run: WorkflowRun) -> tuple[str, str | None]:
    """Return the persisted coordinator origin used after worker handoff."""
    value = run.outputs.get(WORKFLOW_COORDINATION_OUTPUT)
    coordination = value if isinstance(value, Mapping) else {}
    origin = str(coordination.get("origin") or "manual")
    schedule_id = _optional_text(coordination.get("schedule_id"))
    if origin not in {"manual", "interactive", "scheduled"}:
        return "manual", None
    return origin, schedule_id


class WorkflowHandoffManager:
    """Coordinate explicit patch selection, merge, apply, and cleanup actions."""

    def __init__(self, coordinator: WorkflowCoordinator) -> None:
        self.coordinator = coordinator
        self.repository = coordinator.repository

    def status(self, run_id: str) -> dict[str, Any]:
        """Return selected edit steps, file conflicts, and merge state."""
        run = self.repository.get_run(run_id)
        steps = self.repository.list_steps(run_id)
        candidates: list[dict[str, Any]] = []
        selected_metadata: dict[str, Mapping[str, Any]] = {}
        for step in steps:
            child_run_id = str(step.outputs.get("run_id") or "")
            if not child_run_id:
                continue
            child_run = self.coordinator.runner.store.get_run(child_run_id)
            execution = _mapping(child_run.metadata.get("workspace_execution"))
            if execution.get("policy") != "worktree":
                continue
            selected = bool(step.outputs.get("handoff_selected"))
            if selected:
                selected_metadata[child_run_id] = child_run.metadata
            candidates.append(
                {
                    "step_id": step.step_id,
                    "run_id": child_run_id,
                    "selected": selected,
                    "changed_files": list(execution.get("changed_files") or ()),
                    "untracked_files": list(execution.get("untracked_files") or ()),
                    "retained": bool(
                        execution.get("worktree_path")
                        and not execution.get("discarded_at")
                    ),
                    "applied": bool(execution.get("applied_at")),
                    "actions": {
                        "choose": f"/api/workflow-runs/{run_id}/handoffs/{step.step_id}/choose",
                        "apply": f"/api/runs/{child_run_id}/apply",
                        "discard": f"/api/workflow-runs/{run_id}/handoffs/{step.step_id}/discard",
                    },
                }
            )
        return {
            "workflow_run_id": run_id,
            "candidates": candidates,
            "conflicts": list(detect_overlapping_run_diffs(selected_metadata)),
            "merge_queue": dict(_mapping(run.outputs.get("_merge_queue"))),
            "actions": {
                "prepare_merge": f"/api/workflow-runs/{run_id}/merge-queue",
                "apply_merge": f"/api/workflow-runs/{run_id}/merge-queue/apply",
            },
        }

    def choose(self, run_id: str, step_id: str, *, selected: bool) -> dict[str, Any]:
        """Choose or remove one retained edit patch from the merge queue."""
        step = self._edit_step(run_id, step_id)
        outputs = {**dict(step.outputs), "handoff_selected": selected}
        self.repository.update_step(step.id, status=step.status, outputs=outputs)
        run = self.repository.get_run(run_id)
        if run.outputs.get("_merge_queue"):
            self.repository.update_run(
                run_id,
                run.status,
                outputs={
                    **dict(run.outputs),
                    "_merge_queue": {
                        "stale": True,
                        "reason": "selection changed",
                    },
                },
            )
        return self.status(run_id)

    def prepare_merge(self, run_id: str) -> dict[str, Any]:
        """Prepare a combined patch without changing the source checkout."""
        run = self.repository.get_run(run_id)
        selected: dict[str, Mapping[str, Any]] = {}
        for step in self.repository.list_steps(run_id):
            if not step.outputs.get("handoff_selected"):
                continue
            child_run_id = str(step.outputs.get("run_id") or "")
            if child_run_id:
                selected[child_run_id] = self.coordinator.runner.store.get_run(
                    child_run_id
                ).metadata
        merged = prepare_run_diff_merge(
            selected,
            data_dir=self.coordinator.runtime_store.data_dir,
            session_id=run.session_id,
            merge_id=f"merge_{run.id}",
        )
        state = {
            "status": "prepared",
            "workspace_execution": merged,
            "source_run_ids": list(merged["source_run_ids"]),
            "changed_files": list(merged.get("changed_files") or ()),
            "prepared_at": merged["prepared_at"],
        }
        self.repository.update_run(
            run.id,
            run.status,
            outputs={**dict(run.outputs), "_merge_queue": state},
        )
        return self.status(run_id)

    def apply_merge(self, run_id: str, *, review: RunDiffReview) -> dict[str, Any]:
        """Apply a previously prepared combined patch to a clean source checkout."""
        run = self.repository.get_run(run_id)
        queue = _mapping(run.outputs.get("_merge_queue"))
        execution = _mapping(queue.get("workspace_execution"))
        if queue.get("status") != "prepared" or not execution:
            raise WorktreeError("Merge queue is not prepared.")
        applied = apply_run_diff(
            {"workspace_execution": execution},
            review=review,
        )
        state = {**dict(queue), "status": "applied", "workspace_execution": applied}
        self.repository.update_run(
            run.id,
            run.status,
            outputs={**dict(run.outputs), "_merge_queue": state},
        )
        return self.status(run_id)

    def discard(self, run_id: str, step_id: str) -> dict[str, Any]:
        """Discard one retained child worktree without applying its patch."""
        step = self._edit_step(run_id, step_id)
        child_run = self.coordinator.runner.store.get_run(
            str(step.outputs.get("run_id") or "")
        )
        execution = discard_run_worktree(child_run.metadata)
        self.coordinator.runner.store.update_run(
            child_run.id,
            metadata={**dict(child_run.metadata), "workspace_execution": execution},
        )
        self.repository.update_step(
            step.id,
            status=step.status,
            outputs={**dict(step.outputs), "handoff_selected": False},
        )
        return self.status(run_id)

    def _edit_step(self, run_id: str, step_id: str) -> StepAttempt:
        for step in self.repository.list_steps(run_id):
            if step.step_id != step_id:
                continue
            child_run_id = str(step.outputs.get("run_id") or "")
            if not child_run_id:
                break
            child_run = self.coordinator.runner.store.get_run(child_run_id)
            execution = _mapping(child_run.metadata.get("workspace_execution"))
            if execution.get("policy") == "worktree":
                return step
            break
        raise WorktreeError(f"Workflow step {step_id} has no retained edit patch.")
