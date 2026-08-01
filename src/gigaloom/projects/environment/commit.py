"""Governed environment commit service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Mapping

from .models import (
    EnvironmentCaptureError,
    EnvironmentSnapshot,
)
from .capture import GitEnvironmentProvider
from gigaloom.runtime import policy as runtime_policy
from gigaloom.runtime import store as runtime_store
from . import commit_io as _commit_io
from .commit_contracts import (
    ENVIRONMENT_COMMIT_OWNER,
    ENVIRONMENT_COMMIT_SCHEMA_VERSION,
    EnvironmentCommitError,
    EnvironmentCommitPreview,
    EnvironmentCommitResult,
    GIT_MUTATION_TIMEOUT_SECONDS,
    MAX_AUTHOR_CHARS,
    MAX_COMMIT_MESSAGE_CHARS,
    MAX_GIT_OUTPUT_BYTES,
    _HEX_SHA_RE,
    _mapping_hash,
    _utc_now,
    _validate_author,
    _validate_email,
    _validate_message,
    _validate_preview_id,
    _write_private_json,
)

from .commit_io import (
    _CommandResult,
    _run_bounded,
    _snapshot_matches_preview,
)

EnforcementLevel = runtime_policy.EnforcementLevel
INTERACTIVE_PROFILE = runtime_policy.INTERACTIVE_PROFILE
PermissionAction = runtime_policy.PermissionAction
PolicyContext = runtime_policy.PolicyContext
PolicyDecision = runtime_policy.PolicyDecision
PolicyEngine = runtime_policy.PolicyEngine
RuntimeCoordinationStore = runtime_store.RuntimeCoordinationStore
subprocess = _commit_io.subprocess

__all__ = [
    "ENVIRONMENT_COMMIT_OWNER",
    "ENVIRONMENT_COMMIT_SCHEMA_VERSION",
    "GIT_MUTATION_TIMEOUT_SECONDS",
    "MAX_AUTHOR_CHARS",
    "MAX_COMMIT_MESSAGE_CHARS",
    "MAX_GIT_OUTPUT_BYTES",
    "EnvironmentCommitError",
    "EnvironmentCommitOutcome",
    "EnvironmentCommitPreview",
    "EnvironmentCommitResult",
    "EnvironmentCommitService",
    "GovernedEnvironmentCommitService",
]


class EnvironmentCommitService:
    """Preview and apply deterministic local commits through one Git authority."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        git_executable: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        executable = git_executable or shutil.which("git")
        if executable is None:
            raise EnvironmentCommitError("git_unavailable", "Git is unavailable.")
        resolved = Path(executable).expanduser().resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise EnvironmentCommitError("git_unavailable", "Git is unavailable.")
        self._git = str(resolved)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._provider = GitEnvironmentProvider(
            git_executable=self._git,
            clock=self._clock,
        )
        self._root = Path(data_dir).expanduser().resolve() / "environment_commits"
        self._previews = self._root / "previews"
        self._results = self._root / "results"
        self._lock = threading.RLock()

    def preview(
        self,
        workspace: str | Path,
        *,
        message: str,
        author_name: str,
        author_email: str,
    ) -> EnvironmentCommitPreview:
        """Persist or reuse the immutable preview for one exact staged state."""
        message = _validate_message(message)
        author_name = _validate_author(author_name, "author name")
        author_email = _validate_email(author_email)
        snapshot = self._capture(workspace)
        if snapshot.staged_count < 1:
            raise EnvironmentCommitError(
                "no_staged_changes", "A commit requires staged changes."
            )
        if snapshot.detached or snapshot.branch is None:
            raise EnvironmentCommitError(
                "detached_head", "A governed commit requires an attached branch."
            )
        now = self._clock().astimezone(timezone.utc).replace(microsecond=0)
        created_at = now.isoformat().replace("+00:00", "Z")
        semantic = {
            "schema_version": ENVIRONMENT_COMMIT_SCHEMA_VERSION,
            "repository_root": snapshot.repository_root,
            "worktree_root": snapshot.worktree_root,
            "branch": snapshot.branch,
            "head": snapshot.head,
            "diff_sha256": snapshot.diff_sha256,
            "remote": snapshot.remote,
            "staged_count": snapshot.staged_count,
            "message": message,
            "author_name": author_name,
            "author_email": author_email,
        }
        preview_id = f"commit_{_mapping_hash(semantic)}"
        path = self._preview_path(preview_id)
        with self._lock:
            if path.is_file():
                return self.get_preview(preview_id)
            preview = EnvironmentCommitPreview(
                id=preview_id,
                repository_root=snapshot.repository_root,
                worktree_root=snapshot.worktree_root,
                branch=snapshot.branch,
                head=snapshot.head,
                diff_sha256=snapshot.diff_sha256,
                remote=snapshot.remote,
                staged_count=snapshot.staged_count,
                message=message,
                author_name=author_name,
                author_email=author_email,
                commit_date=f"{int(now.timestamp())} +0000",
                created_at=created_at,
            )
            _write_private_json(path, preview.to_dict())
            return preview

    def get_preview(self, preview_id: str) -> EnvironmentCommitPreview:
        """Load one exact private preview."""
        path = self._preview_path(preview_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EnvironmentCommitError(
                "preview_not_found", "Commit preview was not found."
            ) from exc
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise EnvironmentCommitError(
                "preview_invalid", "Commit preview is unavailable."
            ) from exc
        if not isinstance(payload, Mapping):
            raise EnvironmentCommitError(
                "preview_invalid", "Commit preview is unavailable."
            )
        try:
            return EnvironmentCommitPreview.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise EnvironmentCommitError(
                "preview_invalid", "Commit preview is unavailable."
            ) from exc

    def completed_result(self, preview_id: str) -> EnvironmentCommitResult | None:
        """Return existing completion evidence without mutating Git."""
        path = self._result_path(preview_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("result record is invalid")
            return EnvironmentCommitResult.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EnvironmentCommitError(
                "result_invalid", "Commit completion evidence is unavailable."
            ) from exc

    def validate_current(self, preview: EnvironmentCommitPreview) -> None:
        """Fail before approval consumption when the immutable preview is stale."""
        if self.completed_result(preview.id) is not None:
            return
        recovered = self._recover_if_committed(preview)
        if recovered is not None:
            return
        current = self._capture(preview.worktree_root)
        if not _snapshot_matches_preview(current, preview):
            raise EnvironmentCommitError(
                "stale_preview", "Git state changed after the commit preview."
            )

    def apply(self, preview_id: str) -> EnvironmentCommitResult:
        """Create one deterministic commit or return its durable prior result."""
        with self._lock:
            preview = self.get_preview(preview_id)
            completed = self.completed_result(preview.id)
            if completed is not None:
                return completed
            recovered = self._recover_if_committed(preview)
            if recovered is not None:
                return recovered
            current = self._capture(preview.worktree_root)
            if not _snapshot_matches_preview(current, preview):
                raise EnvironmentCommitError(
                    "stale_preview", "Git state changed after the commit preview."
                )
            root = Path(preview.worktree_root)
            tree = self._required_sha(root, "write-tree")
            confirmed = self._capture(preview.worktree_root)
            if not _snapshot_matches_preview(confirmed, preview):
                raise EnvironmentCommitError(
                    "stale_preview", "Git state changed while preparing the commit."
                )
            commit = self._create_commit(preview, tree, write=True)
            old = preview.head or ("0" * 40)
            update = self._run(
                root,
                "update-ref",
                "-m",
                "giga governed environment commit",
                "HEAD",
                commit,
                old,
            )
            if update.returncode != 0:
                recovered = self._recover_if_committed(preview)
                if recovered is not None:
                    return recovered
                raise EnvironmentCommitError(
                    "commit_conflict", "Git state changed while committing."
                )
            result = EnvironmentCommitResult(
                preview_id=preview.id,
                branch=preview.branch,
                parent_head=preview.head,
                commit_head=commit,
                completed_at=_utc_now(self._clock),
            )
            _write_private_json(self._result_path(preview.id), result.to_dict())
            return result

    def _recover_if_committed(
        self, preview: EnvironmentCommitPreview
    ) -> EnvironmentCommitResult | None:
        root = Path(preview.worktree_root)
        head = self._optional_sha(root, "rev-parse", "--verify", "HEAD")
        if head is None or head == preview.head:
            return None
        parent = self._optional_sha(root, "rev-parse", "--verify", f"{head}^")
        if parent != preview.head:
            return None
        tree = self._optional_sha(root, "rev-parse", "--verify", f"{head}^{{tree}}")
        if tree is None:
            return None
        expected = self._create_commit(preview, tree, write=False)
        if expected != head:
            return None
        result = EnvironmentCommitResult(
            preview_id=preview.id,
            branch=preview.branch,
            parent_head=preview.head,
            commit_head=head,
            completed_at=_utc_now(self._clock),
            recovered=True,
        )
        _write_private_json(self._result_path(preview.id), result.to_dict())
        return result

    def _create_commit(
        self,
        preview: EnvironmentCommitPreview,
        tree: str,
        *,
        write: bool,
    ) -> str:
        headers = [f"tree {tree}"]
        if preview.head is not None:
            headers.append(f"parent {preview.head}")
        identity = (
            f"{preview.author_name} <{preview.author_email}> {preview.commit_date}"
        )
        headers.extend((f"author {identity}", f"committer {identity}"))
        content = "\n".join(headers) + "\n\n" + preview.message.rstrip("\n") + "\n"
        args = ["hash-object", "-t", "commit"]
        if write:
            args.append("-w")
        args.append("--stdin")
        result = self._run(
            Path(preview.worktree_root),
            *args,
            input_bytes=content.encode("utf-8"),
        )
        if result.returncode != 0:
            raise EnvironmentCommitError("git_failed", "Git commit creation failed.")
        value = result.stdout.decode("ascii", "replace").strip()
        if _HEX_SHA_RE.fullmatch(value) is None:
            raise EnvironmentCommitError(
                "git_output_invalid", "Git commit output is invalid."
            )
        return value

    def _required_sha(self, root: Path, *args: str) -> str:
        value = self._optional_sha(root, *args)
        if value is None:
            raise EnvironmentCommitError("git_failed", "Git commit preparation failed.")
        return value

    def _optional_sha(self, root: Path, *args: str) -> str | None:
        result = self._run(root, *args)
        if result.returncode != 0:
            return None
        value = result.stdout.decode("ascii", "replace").strip()
        return value if _HEX_SHA_RE.fullmatch(value) else None

    def _run(
        self,
        root: Path,
        *args: str,
        input_bytes: bytes | None = None,
    ) -> _CommandResult:
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        return _run_bounded(
            (self._git, "--no-optional-locks", "-C", str(root), *args),
            environment=environment,
            input_bytes=input_bytes,
        )

    def _capture(self, workspace: str | Path) -> EnvironmentSnapshot:
        try:
            return self._provider.snapshot(workspace)
        except EnvironmentCaptureError as exc:
            raise EnvironmentCommitError(exc.code, str(exc)) from exc

    def _preview_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._previews / f"{preview_id}.json"

    def _result_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._results / f"{preview_id}.json"


@dataclass(frozen=True)
class EnvironmentCommitOutcome:
    """One governed request outcome shared by product surfaces."""

    preview: EnvironmentCommitPreview
    result: EnvironmentCommitResult | None = None
    approval: Any | None = None
    idempotent_replay: bool = False


class GovernedEnvironmentCommitService:
    """Keep policy, approval, stale checks, and Git mutation under one owner."""

    def __init__(
        self,
        commit_service: EnvironmentCommitService,
        runtime_store: RuntimeCoordinationStore,
        policy_engine: PolicyEngine,
    ) -> None:
        self.commit_service = commit_service
        self.runtime_store = runtime_store
        self.policy_engine = policy_engine

    def apply_or_request(
        self,
        preview_id: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> EnvironmentCommitOutcome:
        """Apply a matching grant or create one exact allow-once approval."""
        preview = self.commit_service.get_preview(preview_id)
        completed = self.commit_service.completed_result(preview.id)
        if completed is not None:
            return EnvironmentCommitOutcome(
                preview=preview,
                result=completed,
                idempotent_replay=True,
            )
        self.commit_service.validate_current(preview)
        context = PolicyContext(
            project_id=project_id or preview.scope_id,
            session_id=session_id,
            reason="Create the exact reviewed local Git commit.",
            preview=preview.to_dict(),
            approval_binding=preview.approval_binding,
            enforcement_owner=ENVIRONMENT_COMMIT_OWNER,
        )
        resolution = self.policy_engine.resolve(
            PermissionAction.GIT_COMMIT,
            profile=INTERACTIVE_PROFILE,
            context=context,
            enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        )
        if resolution.decision is PolicyDecision.DENY:
            raise EnvironmentCommitError("policy_denied", "Commit denied by policy.")
        if resolution.decision is PolicyDecision.ASK:
            approval = self.runtime_store.create_approval_request(resolution, context)
            return EnvironmentCommitOutcome(preview=preview, approval=approval)
        result = self.commit_service.apply(preview.id)
        return EnvironmentCommitOutcome(preview=preview, result=result)
