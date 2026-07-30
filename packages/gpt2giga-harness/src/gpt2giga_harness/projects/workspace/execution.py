"""Preparation of isolated workspace execution contexts."""

from __future__ import annotations

from pathlib import Path
import shutil

from .contracts import (
    WorkspaceExecution,
    WorkspacePolicy,
    WorktreeError,
    parse_workspace_policy,
)
from .git import (
    _git_output,
    _git_run,
    _stderr,
    _worktree_path,
)


def prepare_workspace_execution(
    *,
    requested_policy: WorkspacePolicy | str | None,
    harness_kind: str,
    mode: str,
    workspace: str | None,
    data_dir: str | Path,
    session_id: str,
    run_id: str,
    dry_run: bool = False,
) -> WorkspaceExecution:
    """Prepare the effective workspace for one harness run."""
    parsed_policy = parse_workspace_policy(requested_policy)
    source_workspace = str(Path(workspace).expanduser()) if workspace else None
    if source_workspace is None or mode != "edit" or dry_run:
        return WorkspaceExecution(
            requested_policy=parsed_policy,
            policy=WorkspacePolicy.CURRENT,
            source_workspace=source_workspace,
        )

    should_use_worktree = parsed_policy == WorkspacePolicy.WORKTREE or (
        parsed_policy == WorkspacePolicy.AUTO and harness_kind == "agent-cli"
    )
    if parsed_policy == WorkspacePolicy.TEMP_COPY:
        raise WorktreeError(
            "temp_copy workspace isolation is not implemented; refusing to run "
            "in the current workspace."
        )
    if not should_use_worktree:
        return WorkspaceExecution(
            requested_policy=parsed_policy,
            policy=WorkspacePolicy.CURRENT,
            source_workspace=source_workspace,
        )

    git_root = _git_output(source_workspace, "rev-parse", "--show-toplevel")
    if git_root is None:
        raise WorktreeError(
            "Workspace isolation requires a Git repository; refusing to run in "
            "the current workspace."
        )
    base_commit = _git_output(git_root, "rev-parse", "HEAD")
    if base_commit is None:
        raise WorktreeError(
            "Workspace isolation requires a Git base commit; refusing to run in "
            "the current workspace."
        )
    base_branch = _git_output(git_root, "branch", "--show-current")
    worktree_path = _worktree_path(data_dir, session_id, run_id)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    if worktree_path.exists():
        removed = _git_run(
            git_root,
            "worktree",
            "remove",
            "--force",
            str(worktree_path),
            timeout=30,
        )
        if removed.returncode != 0 and worktree_path.exists():
            shutil.rmtree(worktree_path)
            _git_run(git_root, "worktree", "prune", timeout=30)
    created = _git_run(
        git_root,
        "worktree",
        "add",
        "--detach",
        str(worktree_path),
        base_commit,
        timeout=30,
    )
    if created.returncode != 0:
        reason = _stderr(created) or "git worktree add failed"
        raise WorktreeError(
            f"Could not create an isolated Git worktree ({reason}); refusing to "
            "run in the current workspace."
        )
    return WorkspaceExecution(
        requested_policy=parsed_policy,
        policy=WorkspacePolicy.WORKTREE,
        source_workspace=source_workspace,
        source_git_root=git_root,
        effective_workspace=str(worktree_path),
        worktree_path=str(worktree_path),
        base_branch=base_branch,
        base_commit=base_commit,
    )
