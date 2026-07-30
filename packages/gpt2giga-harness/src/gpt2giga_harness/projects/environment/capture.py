"""Canonical bounded Git environment capture service."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
from typing import Callable


from .models import (
    EnvironmentCaptureError,
    EnvironmentProviderDescriptor,
    EnvironmentSnapshot,
    GIT_TIMEOUT_SECONDS,
    HostedRepositoryHint,
    MAX_CAPTURED_PATHS,
    MAX_CHANGED_PATHS,
    MAX_COMMAND_OUTPUT_BYTES,
    MAX_DIFF_HASH_BYTES,
    _HEX_SHA_RE,
    _is_safe_summary_path,
)

from .capture_io import (
    _CommandResult,
    _hash_command_output,
    _hash_untracked_files,
    _is_safe_ref_text,
    _parse_hosted_repository,
    _parse_status,
    _push_blocker,
    _run_bounded_command,
)

GIT_ENVIRONMENT_DESCRIPTOR = EnvironmentProviderDescriptor(
    id="git",
    display_name="Git",
    capabilities=("local_snapshot",),
)


class GitEnvironmentProvider:
    """Capture bounded, read-only local Git snapshots without retaining content."""

    descriptor = GIT_ENVIRONMENT_DESCRIPTOR

    def __init__(
        self,
        *,
        git_executable: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        executable = git_executable or shutil.which("git")
        if executable is None:
            raise EnvironmentCaptureError("git_unavailable", "Git is unavailable.")
        resolved = Path(executable).expanduser().resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise EnvironmentCaptureError(
                "git_unavailable", "Git executable is unavailable."
            )
        self._git = str(resolved)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def snapshot(self, workspace: str | Path) -> EnvironmentSnapshot:
        """Capture one exact bounded snapshot of a local Git worktree."""
        requested = Path(workspace).expanduser().resolve()
        if not requested.is_dir():
            raise EnvironmentCaptureError(
                "workspace_unavailable", "Workspace is not a directory."
            )
        worktree_root = self._required_text(requested, "rev-parse", "--show-toplevel")
        worktree_path = Path(worktree_root).resolve()
        common_git_dir = self._required_text(
            worktree_path, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        common_path = Path(common_git_dir).resolve()
        repository_path = (
            common_path.parent if common_path.name == ".git" else worktree_path
        )

        status_result = self._run(
            worktree_path,
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude)local",
            ":(exclude)local/**",
        )
        status = _parse_status(status_result.stdout)
        safe_paths = tuple(
            sorted(path for path in status.paths if _is_safe_summary_path(path))
        )
        if len(safe_paths) > MAX_CAPTURED_PATHS:
            raise EnvironmentCaptureError(
                "path_limit", "Git environment contains too many changed paths."
            )

        head = status.head
        upstream = status.upstream
        base_identity = head
        if head is not None and upstream is not None:
            base = self._optional_text(worktree_path, "merge-base", "HEAD", upstream)
            if base is not None and _HEX_SHA_RE.fullmatch(base):
                base_identity = base
        remote = self._remote_name(worktree_path, status.branch, upstream)
        additions, deletions = self._numstat(worktree_path, head, safe_paths)
        diff_sha256 = self._diff_hash(
            worktree_path,
            head,
            safe_paths,
            tuple(
                path for path in status.untracked_paths if _is_safe_summary_path(path)
            ),
            status_result.stdout,
        )
        summary = safe_paths[:MAX_CHANGED_PATHS]
        push_blocker = _push_blocker(
            branch=status.branch,
            detached=status.detached,
            head=head,
            remote=remote,
        )
        captured_at = (
            self._clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        return EnvironmentSnapshot(
            provider_id=self.descriptor.id,
            repository_root=str(repository_path),
            worktree_root=str(worktree_path),
            branch=status.branch,
            detached=status.detached,
            head=head,
            base_identity=base_identity,
            upstream=upstream,
            ahead=status.ahead,
            behind=status.behind,
            remote=remote,
            staged_count=status.staged_count,
            unstaged_count=status.unstaged_count,
            untracked_count=status.untracked_count,
            additions=additions,
            deletions=deletions,
            changed_paths=summary,
            changed_paths_truncated=len(safe_paths) > len(summary),
            diff_sha256=diff_sha256,
            captured_at=captured_at,
            push_ready=push_blocker is None,
            push_blocker=push_blocker,
        )

    def hosted_repository(
        self, snapshot: EnvironmentSnapshot
    ) -> HostedRepositoryHint | None:
        """Resolve one credential-free remote identity without exposing its URL."""
        if snapshot.provider_id != self.descriptor.id or snapshot.remote is None:
            return None
        root = Path(snapshot.worktree_root).expanduser().resolve()
        remote_url = self._optional_text(
            root, "remote", "get-url", "--", snapshot.remote
        )
        if remote_url is None:
            return None
        return _parse_hosted_repository(remote_url)

    def _remote_name(
        self,
        root: Path,
        branch: str | None,
        upstream: str | None,
    ) -> str | None:
        remote = None
        if branch is not None:
            remote = self._optional_text(
                root, "config", "--get", f"branch.{branch}.remote"
            )
        if remote in {None, "."} and upstream and "/" in upstream:
            remote = upstream.split("/", 1)[0]
        if remote in {None, "."}:
            remotes = self._optional_text(root, "remote")
            values = tuple(item for item in (remotes or "").splitlines() if item)
            remote = values[0] if len(values) == 1 else None
        if remote is not None and not _is_safe_ref_text(remote):
            return None
        return remote

    def _numstat(
        self, root: Path, head: str | None, paths: tuple[str, ...]
    ) -> tuple[int, int]:
        if head is None or not paths:
            return 0, 0
        result = self._run(
            root,
            "diff",
            "--numstat",
            "--no-renames",
            "-z",
            "HEAD",
            "--",
            *paths,
        )
        additions = 0
        deletions = 0
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            fields = record.split(b"\t", 2)
            if len(fields) != 3:
                raise EnvironmentCaptureError(
                    "git_output_invalid", "Git numstat output is invalid."
                )
            if fields[0].isdigit():
                additions += int(fields[0])
            if fields[1].isdigit():
                deletions += int(fields[1])
        return additions, deletions

    def _diff_hash(
        self,
        root: Path,
        head: str | None,
        paths: tuple[str, ...],
        untracked_paths: tuple[str, ...],
        status_bytes: bytes,
    ) -> str:
        digest = hashlib.sha256()
        digest.update(b"environment-diff-v1\0")
        digest.update(status_bytes)
        _hash_untracked_files(root, untracked_paths, digest)
        if head is None or not paths:
            return digest.hexdigest()
        command = (
            self._git,
            "--no-optional-locks",
            "-C",
            str(root),
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            "HEAD",
            "--",
            *paths,
        )
        return _hash_command_output(
            command,
            digest=digest,
            timeout=GIT_TIMEOUT_SECONDS,
            max_bytes=MAX_DIFF_HASH_BYTES,
        )

    def _required_text(self, root: Path, *args: str) -> str:
        value = self._optional_text(root, *args)
        if value is None:
            raise EnvironmentCaptureError(
                "not_git_repository", "Workspace is not a supported Git worktree."
            )
        return value

    def _optional_text(self, root: Path, *args: str) -> str | None:
        result = self._run(root, *args, allowed_returncodes=(0, 1, 128))
        if result.returncode != 0:
            return None
        value = result.stdout.decode("utf-8", "replace").strip()
        return value or None

    def _run(
        self,
        root: Path,
        *args: str,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> _CommandResult:
        command = (
            self._git,
            "--no-optional-locks",
            "-C",
            str(root),
            *args,
        )
        result = _run_bounded_command(
            command,
            timeout=GIT_TIMEOUT_SECONDS,
            max_output_bytes=MAX_COMMAND_OUTPUT_BYTES,
        )
        if result.returncode not in allowed_returncodes:
            raise EnvironmentCaptureError(
                "git_failed", "Git environment inspection failed."
            )
        return result
