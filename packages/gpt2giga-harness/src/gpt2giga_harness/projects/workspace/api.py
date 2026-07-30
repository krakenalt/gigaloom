"""Public workspace resolution, file, and worktree boundary."""

from .contracts import (
    RunDiffReview,
    WorkspaceDiff,
    WorkspaceExecution,
    WorkspacePolicy,
    WorktreeConflictError,
    WorktreeError,
    parse_workspace_policy,
)
from .execution import prepare_workspace_execution
from .files import workspace_file_metadata
from .resolver import resolve_workspace
from .tree import workspace_tree
from .worktrees import (
    MAX_PATCH_CHARS,
    apply_run_diff,
    capture_workspace_diff,
    detect_overlapping_run_diffs,
    discard_run_worktree,
    open_worktree_response,
    prepare_run_diff_merge,
    review_run_diff,
    run_diff_response,
    shlex_quote,
)

__all__ = [
    "MAX_PATCH_CHARS",
    "RunDiffReview",
    "WorkspaceDiff",
    "WorkspaceExecution",
    "WorkspacePolicy",
    "WorktreeConflictError",
    "WorktreeError",
    "apply_run_diff",
    "capture_workspace_diff",
    "detect_overlapping_run_diffs",
    "discard_run_worktree",
    "open_worktree_response",
    "parse_workspace_policy",
    "prepare_run_diff_merge",
    "prepare_workspace_execution",
    "resolve_workspace",
    "review_run_diff",
    "run_diff_response",
    "shlex_quote",
    "workspace_file_metadata",
    "workspace_tree",
]
