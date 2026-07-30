"""Workspace isolation policies and immutable diff contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class WorkspacePolicy(str, Enum):
    """Workspace execution policies understood by the harness runner."""

    AUTO = "auto"
    CURRENT = "current"
    WORKTREE = "worktree"
    TEMP_COPY = "temp_copy"


class WorktreeError(ValueError):
    """Raised when worktree metadata cannot be used."""


class WorktreeConflictError(WorktreeError):
    """Raised when applying a patch would be unsafe."""


@dataclass(frozen=True)
class WorkspaceExecution:
    """Prepared workspace execution context for one run."""

    requested_policy: WorkspacePolicy
    policy: WorkspacePolicy
    source_workspace: str | None
    source_git_root: str | None = None
    effective_workspace: str | None = None
    worktree_path: str | None = None
    base_branch: str | None = None
    base_commit: str | None = None
    fallback_reason: str | None = None

    @property
    def request_workspace(self) -> str | None:
        """Return the workspace path that should be passed to the harness."""
        return self.effective_workspace or self.source_workspace

    def to_metadata(self) -> dict[str, Any]:
        """Serialize the execution context for run metadata."""
        return {
            "requested_policy": self.requested_policy.value,
            "policy": self.policy.value,
            "source_workspace": self.source_workspace,
            "source_git_root": self.source_git_root,
            "effective_workspace": self.effective_workspace,
            "worktree_path": self.worktree_path,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class WorkspaceDiff:
    """Captured diff metadata for one workspace."""

    patch: str
    changed_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    captured: bool = False
    truncated: bool = False
    error: str | None = None

    def to_metadata(self) -> dict[str, Any]:
        """Serialize diff metadata for a run."""
        return {
            "patch": self.patch,
            "changed_files": list(self.changed_files),
            "untracked_files": list(self.untracked_files),
            "captured": self.captured,
            "truncated": self.truncated,
            "error": self.error,
        }


@dataclass(frozen=True)
class RunDiffReview:
    """Immutable identity of one patch promotion reviewed by an operator."""

    source_sha: str
    patch_sha256: str
    approval_binding: str
    approval_binding_sha256: str
    changed_files: tuple[str, ...] = ()
    untracked_files: tuple[str, ...] = ()
    branch_name: str | None = None

    def to_preview(self) -> dict[str, Any]:
        """Serialize the exact source, patch, and target intent for approval."""
        return {
            "source_sha": self.source_sha,
            "patch_sha256": self.patch_sha256,
            "approval_binding_sha256": self.approval_binding_sha256,
            "branch_name": self.branch_name,
            "changed_files": list(self.changed_files),
            "untracked_files": list(self.untracked_files),
        }


def parse_workspace_policy(value: Any) -> WorkspacePolicy:
    """Parse a workspace policy from CLI/API payloads."""
    if isinstance(value, WorkspacePolicy):
        return value
    if value is None or not str(value).strip():
        return WorkspacePolicy.AUTO
    normalized = str(value).strip().lower().replace("-", "_")
    return WorkspacePolicy(normalized)
