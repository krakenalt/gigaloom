"""Review apply primitives."""

from __future__ import annotations

from typing import Any
from gigaloom.review.ports import HarnessRun
from gigaloom.projects.api import RunDiffReview, apply_run_diff
from .preview import _text, build_pr_artifact


def create_pr_branch(
    run: HarnessRun,
    *,
    review: RunDiffReview,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Create a local branch from a worktree-backed run and apply its patch."""
    artifact = build_pr_artifact(run)
    clean_branch_name = _text(branch_name) or artifact.branch_name_suggestion
    workspace_execution = apply_run_diff(
        run.metadata,
        review=review,
        branch_name=clean_branch_name,
    )
    return {
        "branch_name": clean_branch_name,
        "workspace_execution": workspace_execution,
    }
