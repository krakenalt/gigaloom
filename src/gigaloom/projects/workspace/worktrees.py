"""Reviewed capture, apply, discard, and merge of workspace diffs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping

from gigaloom.sessions import store as session_store

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
from .git import (
    _git_apply,
    _git_output,
    _git_run,
    _git_status,
    _optional_branch_name,
    _required_metadata_text,
    _stderr,
    _string_list,
    _workspace_execution_metadata,
)

utc_now = session_store.utc_now


MAX_PATCH_CHARS = 200_000


def capture_workspace_diff(execution: WorkspaceExecution) -> WorkspaceDiff | None:
    """Capture a git diff for the prepared workspace."""
    workspace = execution.request_workspace
    if workspace is None:
        return None
    git_root = _git_output(workspace, "rev-parse", "--show-toplevel")
    if git_root is None:
        return WorkspaceDiff(
            patch="No diff captured.",
            error="workspace is not inside a git repository",
        )
    status = _git_status(git_root)
    if status is None:
        return WorkspaceDiff(patch="No diff captured.", error="git status failed")
    untracked = tuple(path for code, path in status if code == "??")
    changed = tuple(path for code, path in status if code != "??")
    diff = _capture_git_diff(git_root, untracked)
    if diff.returncode != 0:
        return WorkspaceDiff(
            patch="No diff captured.",
            changed_files=changed,
            untracked_files=untracked,
            error=_stderr(diff) or "git diff failed",
        )
    patch, truncated = _bounded_text(diff.stdout)
    captured = bool(patch.strip())
    return WorkspaceDiff(
        patch=patch if captured else "No diff captured.",
        changed_files=changed,
        untracked_files=untracked,
        captured=captured,
        truncated=truncated,
    )


def run_diff_response(run_metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return an API-friendly diff payload from stored run metadata."""
    execution = _workspace_execution_metadata(run_metadata)
    patch = str(execution.get("patch") or run_metadata.get("diff") or "")
    changed_files = _string_list(execution.get("changed_files"))
    untracked_files = _string_list(execution.get("untracked_files"))
    return {
        "workspace_execution": dict(execution),
        "patch": patch,
        "changed_files": changed_files,
        "untracked_files": untracked_files,
        "can_apply": _can_apply(execution, patch),
        "can_discard": _can_discard(execution),
    }


def review_run_diff(
    run_metadata: Mapping[str, Any],
    *,
    branch_name: str | None = None,
) -> RunDiffReview:
    """Freeze the source, captured patch, and branch intent shown for approval."""
    execution = _workspace_execution_metadata(run_metadata)
    if execution.get("policy") != WorkspacePolicy.WORKTREE.value:
        raise WorktreeError("Run did not use an isolated worktree.")
    if execution.get("applied_at"):
        raise WorktreeError("Run diff has already been applied.")
    if execution.get("discarded_at"):
        raise WorktreeError("Run worktree has already been discarded.")
    if execution.get("truncated"):
        raise WorktreeError("Run patch is truncated and cannot be applied safely.")
    source_root = _required_metadata_text(execution, "source_git_root")
    base_commit = _required_metadata_text(execution, "base_commit")
    patch = _required_metadata_text(execution, "patch")
    if patch == "No diff captured.":
        raise WorktreeError("Run has no captured patch to apply.")
    current_head = _git_output(source_root, "rev-parse", "HEAD")
    if current_head != base_commit:
        raise WorktreeConflictError(
            "Target checkout is not at the run base commit; refusing to apply."
        )
    dirty = _git_run(source_root, "status", "--porcelain=v1", timeout=10)
    if dirty.returncode != 0:
        raise WorktreeError("Could not inspect target checkout status.")
    if dirty.stdout.strip():
        raise WorktreeConflictError(
            "Target checkout has local changes; refusing to apply."
        )
    clean_branch = _optional_branch_name(branch_name)
    check = _git_apply(source_root, patch, "--check")
    if check.returncode != 0:
        raise WorktreeConflictError(_stderr(check) or "git apply --check failed")
    patch_sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    source_root_sha256 = hashlib.sha256(
        str(Path(source_root).resolve()).encode("utf-8")
    ).hexdigest()
    binding_payload = json.dumps(
        {
            "branch_name": clean_branch,
            "patch_sha256": patch_sha256,
            "schema": "gpt2giga.reviewed_patch.v1",
            "source_root_sha256": source_root_sha256,
            "source_sha": current_head,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return RunDiffReview(
        source_sha=current_head,
        patch_sha256=patch_sha256,
        approval_binding=binding_payload,
        approval_binding_sha256=hashlib.sha256(
            binding_payload.encode("utf-8")
        ).hexdigest(),
        changed_files=tuple(_string_list(execution.get("changed_files"))),
        untracked_files=tuple(_string_list(execution.get("untracked_files"))),
        branch_name=clean_branch,
    )


def apply_run_diff(
    run_metadata: Mapping[str, Any],
    *,
    review: RunDiffReview,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Apply one captured worktree patch back to its source git checkout."""
    execution = _workspace_execution_metadata(run_metadata)
    current_review = review_run_diff(run_metadata, branch_name=branch_name)
    if current_review != review:
        raise WorktreeConflictError(
            "Reviewed source, patch, or branch identity changed; refusing to apply."
        )
    source_root = _required_metadata_text(execution, "source_git_root")
    patch = _required_metadata_text(execution, "patch")
    clean_branch = current_review.branch_name
    check = _git_apply(source_root, patch, "--check")
    if check.returncode != 0:
        raise WorktreeConflictError(_stderr(check) or "git apply --check failed")
    if clean_branch:
        created = _git_run(
            source_root,
            "switch",
            "-c",
            clean_branch,
            review.source_sha,
            timeout=20,
        )
        if created.returncode != 0:
            raise WorktreeConflictError(_stderr(created) or "git switch failed")
    applied = _git_apply(source_root, patch)
    if applied.returncode != 0:
        raise WorktreeConflictError(_stderr(applied) or "git apply failed")
    updated = dict(execution)
    updated["applied_at"] = utc_now()
    updated["reviewed_source_sha"] = review.source_sha
    updated["applied_patch_sha256"] = review.patch_sha256
    updated["approval_binding_sha256"] = review.approval_binding_sha256
    if clean_branch:
        updated["applied_branch"] = clean_branch
    return updated


def discard_run_worktree(run_metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Remove an isolated worktree for one run."""
    execution = _workspace_execution_metadata(run_metadata)
    if execution.get("policy") != WorkspacePolicy.WORKTREE.value:
        raise WorktreeError("Run did not use an isolated worktree.")
    worktree_path = _required_metadata_text(execution, "worktree_path")
    source_root = _required_metadata_text(execution, "source_git_root")
    path = Path(worktree_path)
    if path.exists():
        removed = _git_run(
            source_root,
            "worktree",
            "remove",
            "--force",
            worktree_path,
            timeout=30,
        )
        if removed.returncode != 0 and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            _git_run(source_root, "worktree", "prune", timeout=30)
    updated = dict(execution)
    updated["discarded_at"] = utc_now()
    updated["worktree_exists"] = path.exists()
    return updated


def detect_overlapping_run_diffs(
    run_metadatas: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Return conservative file-level conflicts across isolated run patches."""
    owners: dict[str, list[str]] = {}
    for run_id, metadata in run_metadatas.items():
        execution = _workspace_execution_metadata(metadata)
        for path in execution.get("changed_files", ()):
            owners.setdefault(str(path), []).append(str(run_id))
        for path in execution.get("untracked_files", ()):
            owners.setdefault(str(path), []).append(str(run_id))
    return tuple(
        {"path": path, "run_ids": sorted(set(run_ids))}
        for path, run_ids in sorted(owners.items())
        if len(set(run_ids)) > 1
    )


def prepare_run_diff_merge(
    run_metadatas: Mapping[str, Mapping[str, Any]],
    *,
    data_dir: str | Path,
    session_id: str,
    merge_id: str,
) -> dict[str, Any]:
    """Build a reviewable combined patch in a retained isolated worktree."""
    if not run_metadatas:
        raise WorktreeError("Merge queue has no selected run patches.")
    conflicts = detect_overlapping_run_diffs(run_metadatas)
    if conflicts:
        paths = ", ".join(item["path"] for item in conflicts)
        raise WorktreeConflictError(f"Selected patches overlap: {paths}")
    executions = {
        run_id: _workspace_execution_metadata(metadata)
        for run_id, metadata in run_metadatas.items()
    }
    source_roots = {
        _required_metadata_text(execution, "source_git_root")
        for execution in executions.values()
    }
    base_commits = {
        _required_metadata_text(execution, "base_commit")
        for execution in executions.values()
    }
    if len(source_roots) != 1 or len(base_commits) != 1:
        raise WorktreeConflictError(
            "Selected patches do not share one source checkout and base commit."
        )
    for execution in executions.values():
        if execution.get("policy") != WorkspacePolicy.WORKTREE.value:
            raise WorktreeError("Every merge candidate must use an isolated worktree.")
        if execution.get("truncated"):
            raise WorktreeError("A truncated patch cannot enter the merge queue.")
        if execution.get("discarded_at"):
            raise WorktreeError("A discarded worktree cannot enter the merge queue.")
        if not str(execution.get("patch") or "").strip():
            raise WorktreeError("Every merge candidate must contain a captured patch.")
    source_root = next(iter(source_roots))
    merged = prepare_workspace_execution(
        requested_policy=WorkspacePolicy.WORKTREE,
        harness_kind="agent-cli",
        mode="edit",
        workspace=source_root,
        data_dir=data_dir,
        session_id=session_id,
        run_id=merge_id,
    )
    merge_workspace = merged.request_workspace or ""
    for run_id in sorted(executions):
        patch = str(executions[run_id].get("patch") or "")
        checked = _git_apply(merge_workspace, patch, "--check")
        if checked.returncode != 0:
            raise WorktreeConflictError(
                f"Patch {run_id} does not apply cleanly in the merge queue: "
                f"{_stderr(checked) or 'git apply --check failed'}"
            )
        applied = _git_apply(merge_workspace, patch)
        if applied.returncode != 0:
            raise WorktreeConflictError(
                f"Patch {run_id} failed in the merge queue: "
                f"{_stderr(applied) or 'git apply failed'}"
            )
    diff = capture_workspace_diff(merged)
    if diff is None or not diff.captured:
        raise WorktreeError("Merge queue did not produce a combined patch.")
    return {
        **merged.to_metadata(),
        **diff.to_metadata(),
        "source_run_ids": sorted(executions),
        "prepared_at": utc_now(),
        "conflicts": [],
    }


def open_worktree_response(run_metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return the local worktree path and a shell command to enter it."""
    execution = _workspace_execution_metadata(run_metadata)
    path = str(execution.get("worktree_path") or "")
    exists = bool(path and Path(path).exists())
    return {
        "workspace_execution": dict(execution),
        "path": path or None,
        "exists": exists,
        "command": f"cd {shlex_quote(path)}" if path else None,
    }


def shlex_quote(value: str) -> str:
    """Quote a string for display in a POSIX shell."""
    if value and all(char.isalnum() or char in "_./:=@-" for char in value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def _bounded_text(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_PATCH_CHARS:
        return value, False
    return value[-MAX_PATCH_CHARS:], True


def _capture_git_diff(
    git_root: str,
    untracked: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    if not untracked:
        return _git_run(
            git_root,
            "diff",
            "--binary",
            "--no-ext-diff",
            "HEAD",
            "--",
            timeout=20,
        )
    with tempfile.TemporaryDirectory(prefix="gpt2giga-index-") as temporary_dir:
        index_env = {"GIT_INDEX_FILE": str(Path(temporary_dir) / "index")}
        initialized = _git_run(git_root, "read-tree", "HEAD", env=index_env)
        if initialized.returncode != 0:
            return initialized
        add_intent = _git_run(
            git_root,
            "add",
            "-N",
            "--",
            *untracked,
            timeout=10,
            env=index_env,
        )
        if add_intent.returncode != 0:
            return add_intent
        return _git_run(
            git_root,
            "diff",
            "--binary",
            "--no-ext-diff",
            "HEAD",
            "--",
            timeout=20,
            env=index_env,
        )


def _can_apply(execution: Mapping[str, Any], patch: str) -> bool:
    return (
        execution.get("policy") == WorkspacePolicy.WORKTREE.value
        and bool(patch.strip())
        and patch.strip() != "No diff captured."
        and not execution.get("truncated")
        and not execution.get("applied_at")
        and not execution.get("discarded_at")
    )


def _can_discard(execution: Mapping[str, Any]) -> bool:
    return execution.get(
        "policy"
    ) == WorkspacePolicy.WORKTREE.value and not execution.get("discarded_at")


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
    "review_run_diff",
    "run_diff_response",
    "shlex_quote",
]
