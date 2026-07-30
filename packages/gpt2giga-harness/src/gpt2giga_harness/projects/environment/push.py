"""Governed environment push service."""

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
    HostedRepositoryHint,
)
from .capture import GitEnvironmentProvider

from gpt2giga_harness.runtime import policy as runtime_policy
from gpt2giga_harness.runtime import store as runtime_store

from .push_contracts import (
    ENVIRONMENT_PUSH_OWNER,
    ENVIRONMENT_PUSH_SCHEMA_VERSION,
    EnvironmentPushError,
    EnvironmentPushPreview,
    EnvironmentPushResult,
    GIT_PUSH_TIMEOUT_SECONDS,
    MAX_GIT_OUTPUT_BYTES,
    _HEX_SHA_RE,
    _classify_push_failure,
    _mapping_hash,
    _utc_now,
    _validate_preview_id,
    _validate_ref_component,
    _write_private_json,
)

from .push_io import (
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

__all__ = [
    "ENVIRONMENT_PUSH_OWNER",
    "ENVIRONMENT_PUSH_SCHEMA_VERSION",
    "GIT_PUSH_TIMEOUT_SECONDS",
    "MAX_GIT_OUTPUT_BYTES",
    "EnvironmentPushError",
    "EnvironmentPushOutcome",
    "EnvironmentPushPreview",
    "EnvironmentPushResult",
    "EnvironmentPushService",
    "GovernedEnvironmentPushService",
]


class EnvironmentPushService:
    """Preview and apply exact non-force pushes through one Git authority."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        git_executable: str | None = None,
        clock: Callable[[], datetime] | None = None,
        repository_resolver: Callable[
            [EnvironmentSnapshot], HostedRepositoryHint | None
        ]
        | None = None,
    ) -> None:
        executable = git_executable or shutil.which("git")
        if executable is None:
            raise EnvironmentPushError("git_unavailable", "Git is unavailable.")
        resolved = Path(executable).expanduser().resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise EnvironmentPushError("git_unavailable", "Git is unavailable.")
        self._git = str(resolved)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._provider = GitEnvironmentProvider(
            git_executable=self._git,
            clock=self._clock,
        )
        self._repository_resolver = (
            repository_resolver or self._provider.hosted_repository
        )
        self._root = Path(data_dir).expanduser().resolve() / "environment_pushes"
        self._previews = self._root / "previews"
        self._results = self._root / "results"
        self._lock = threading.RLock()

    def preview(self, workspace: str | Path) -> EnvironmentPushPreview:
        """Persist or reuse one remote-state-bound push preview."""
        snapshot = self._capture(workspace)
        branch, head, remote, target_branch = self._push_target(snapshot)
        hint = self._repository_resolver(snapshot)
        if hint is None:
            raise EnvironmentPushError(
                "hosted_remote_required",
                "A governed push requires one supported hosted remote.",
            )
        self._reject_push_url_override(Path(snapshot.worktree_root), remote)
        remote_ref = f"refs/heads/{target_branch}"
        remote_head = self._remote_head(
            Path(snapshot.worktree_root), remote, remote_ref
        )
        semantic = {
            "schema_version": ENVIRONMENT_PUSH_SCHEMA_VERSION,
            "repository_root": snapshot.repository_root,
            "worktree_root": snapshot.worktree_root,
            "repository_host": hint.host,
            "repository_name": hint.name_with_owner,
            "branch": branch,
            "head": head,
            "diff_sha256": snapshot.diff_sha256,
            "remote": remote,
            "upstream": snapshot.upstream,
            "target_branch": target_branch,
            "remote_ref": remote_ref,
            "remote_head": remote_head,
            "ahead": snapshot.ahead,
            "behind": snapshot.behind,
            "set_upstream": snapshot.upstream is None,
        }
        preview_id = f"push_{_mapping_hash(semantic)}"
        path = self._preview_path(preview_id)
        with self._lock:
            if path.is_file():
                return self.get_preview(preview_id)
            preview = EnvironmentPushPreview(
                id=preview_id,
                repository_root=snapshot.repository_root,
                worktree_root=snapshot.worktree_root,
                repository_host=hint.host,
                repository_name=hint.name_with_owner,
                branch=branch,
                head=head,
                diff_sha256=snapshot.diff_sha256,
                remote=remote,
                upstream=snapshot.upstream,
                target_branch=target_branch,
                remote_ref=remote_ref,
                remote_head=remote_head,
                ahead=snapshot.ahead,
                behind=snapshot.behind,
                set_upstream=snapshot.upstream is None,
                created_at=_utc_now(self._clock),
            )
            _write_private_json(path, preview.to_dict())
            return preview

    def get_preview(self, preview_id: str) -> EnvironmentPushPreview:
        """Load one exact private push preview."""
        path = self._preview_path(preview_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("push preview record is invalid")
            return EnvironmentPushPreview.from_dict(payload)
        except FileNotFoundError as exc:
            raise EnvironmentPushError(
                "preview_not_found", "Push preview was not found."
            ) from exc
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EnvironmentPushError(
                "preview_invalid", "Push preview is unavailable."
            ) from exc

    def completed_result(self, preview_id: str) -> EnvironmentPushResult | None:
        """Return existing completion evidence without contacting the remote."""
        path = self._result_path(preview_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("push result record is invalid")
            return EnvironmentPushResult.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EnvironmentPushError(
                "result_invalid", "Push completion evidence is unavailable."
            ) from exc

    def validate_current(self, preview: EnvironmentPushPreview) -> None:
        """Fail before approval consumption when local or remote state is stale."""
        if self.completed_result(preview.id) is not None:
            return
        if self._recover_if_pushed(preview) is not None:
            return
        current = self._capture(preview.worktree_root)
        if not _snapshot_matches_preview(current, preview):
            raise EnvironmentPushError(
                "stale_preview", "Git state changed after the push preview."
            )
        self._validate_remote_identity(current, preview)
        remote_head = self._remote_head(
            Path(preview.worktree_root), preview.remote, preview.remote_ref
        )
        if remote_head != preview.remote_head:
            raise EnvironmentPushError(
                "remote_changed", "Remote Git state changed after the push preview."
            )

    def apply(self, preview_id: str) -> EnvironmentPushResult:
        """Push one exact commit once or recover its durable prior result."""
        with self._lock:
            preview = self.get_preview(preview_id)
            completed = self.completed_result(preview.id)
            if completed is not None:
                return completed
            recovered = self._recover_if_pushed(preview)
            if recovered is not None:
                return recovered
            self.validate_current(preview)
            completed = self.completed_result(preview.id)
            if completed is not None:
                return completed
            root = Path(preview.worktree_root)
            args = [
                "push",
                "--porcelain",
                "--no-verify",
                "--no-follow-tags",
            ]
            if preview.set_upstream:
                args.append("--set-upstream")
            args.extend(("--", preview.remote, f"HEAD:{preview.remote_ref}"))
            result = self._run(root, *args)
            if result.returncode != 0:
                recovered = self._recover_if_pushed(preview)
                if recovered is not None:
                    return recovered
                raise EnvironmentPushError(
                    _classify_push_failure(result.stderr), "Git push failed."
                )
            remote_head = self._remote_head(root, preview.remote, preview.remote_ref)
            if remote_head != preview.head:
                raise EnvironmentPushError(
                    "remote_unconfirmed", "Remote Git push could not be confirmed."
                )
            result_record = self._result(preview, recovered=False)
            _write_private_json(self._result_path(preview.id), result_record.to_dict())
            return result_record

    def _recover_if_pushed(
        self, preview: EnvironmentPushPreview
    ) -> EnvironmentPushResult | None:
        remote_head = self._remote_head(
            Path(preview.worktree_root), preview.remote, preview.remote_ref
        )
        if remote_head != preview.head:
            return None
        result = self._result(preview, recovered=True)
        _write_private_json(self._result_path(preview.id), result.to_dict())
        return result

    def _result(
        self, preview: EnvironmentPushPreview, *, recovered: bool
    ) -> EnvironmentPushResult:
        base = (
            f"https://{preview.repository_host}/{preview.repository_name}"
            f"/commit/{preview.head}"
        )
        current = self._capture(preview.worktree_root)
        return EnvironmentPushResult(
            preview_id=preview.id,
            remote=preview.remote,
            branch=preview.branch,
            target_branch=preview.target_branch,
            remote_ref=preview.remote_ref,
            commit_head=preview.head,
            remote_commit_url=base,
            run_evidence_url=f"{base}/checks",
            upstream_configured=current.upstream
            == f"{preview.remote}/{preview.target_branch}",
            completed_at=_utc_now(self._clock),
            recovered=recovered,
        )

    def _push_target(self, snapshot: EnvironmentSnapshot) -> tuple[str, str, str, str]:
        if snapshot.detached or snapshot.branch is None:
            raise EnvironmentPushError(
                "detached_head", "A governed push requires an attached branch."
            )
        if snapshot.head is None:
            raise EnvironmentPushError("unborn_head", "A push requires a commit.")
        if snapshot.remote is None:
            raise EnvironmentPushError(
                "remote_unavailable", "A push requires one selected remote."
            )
        _validate_ref_component(snapshot.branch, "branch")
        _validate_ref_component(snapshot.remote, "remote")
        target_branch = snapshot.branch
        if snapshot.upstream is not None:
            prefix = f"{snapshot.remote}/"
            if not snapshot.upstream.startswith(prefix):
                raise EnvironmentPushError(
                    "upstream_mismatch",
                    "The selected remote does not own the branch upstream.",
                )
            target_branch = snapshot.upstream.removeprefix(prefix)
        _validate_ref_component(target_branch, "target branch")
        return snapshot.branch, snapshot.head, snapshot.remote, target_branch

    def _reject_push_url_override(self, root: Path, remote: str) -> None:
        result = self._run(root, "config", "--get-all", f"remote.{remote}.pushurl")
        if result.returncode == 0 and result.stdout.strip():
            raise EnvironmentPushError(
                "push_url_override",
                "A separate Git push URL is not supported by governed push.",
            )
        if result.returncode not in {0, 1}:
            raise EnvironmentPushError(
                "git_failed", "Git remote configuration is unavailable."
            )

    def _validate_remote_identity(
        self,
        snapshot: EnvironmentSnapshot,
        preview: EnvironmentPushPreview,
    ) -> None:
        root = Path(snapshot.worktree_root)
        self._reject_push_url_override(root, preview.remote)
        hint = self._repository_resolver(snapshot)
        if (
            hint is None
            or hint.host != preview.repository_host
            or hint.name_with_owner != preview.repository_name
        ):
            raise EnvironmentPushError(
                "remote_changed",
                "Remote Git identity changed after the push preview.",
            )

    def _remote_head(self, root: Path, remote: str, remote_ref: str) -> str | None:
        result = self._run(
            root,
            "ls-remote",
            "--refs",
            "--heads",
            "--exit-code",
            "--",
            remote,
            remote_ref,
        )
        if result.returncode == 2 and not result.stdout.strip():
            return None
        if result.returncode != 0:
            raise EnvironmentPushError(
                "remote_unavailable", "Remote Git state is unavailable."
            )
        records = [record for record in result.stdout.splitlines() if record]
        if len(records) != 1:
            raise EnvironmentPushError(
                "remote_output_invalid", "Remote Git state is invalid."
            )
        fields = records[0].decode("ascii", "replace").split("\t")
        if (
            len(fields) != 2
            or _HEX_SHA_RE.fullmatch(fields[0]) is None
            or fields[1] != remote_ref
        ):
            raise EnvironmentPushError(
                "remote_output_invalid", "Remote Git state is invalid."
            )
        return fields[0]

    def _run(self, root: Path, *args: str) -> _CommandResult:
        environment = dict(os.environ)
        environment.update(
            {
                "GCM_INTERACTIVE": "Never",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_TERMINAL_PROMPT": "0",
                "LC_ALL": "C",
            }
        )
        return _run_bounded(
            (self._git, "--no-optional-locks", "-C", str(root), *args),
            environment=environment,
        )

    def _capture(self, workspace: str | Path) -> EnvironmentSnapshot:
        try:
            return self._provider.snapshot(workspace)
        except EnvironmentCaptureError as exc:
            raise EnvironmentPushError(exc.code, str(exc)) from exc

    def _preview_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._previews / f"{preview_id}.json"

    def _result_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._results / f"{preview_id}.json"


@dataclass(frozen=True)
class EnvironmentPushOutcome:
    """One governed push outcome shared by Web and TUI transports."""

    preview: EnvironmentPushPreview
    result: EnvironmentPushResult | None = None
    approval: Any | None = None
    idempotent_replay: bool = False


class GovernedEnvironmentPushService:
    """Keep policy, approval, stale checks, and remote mutation under one owner."""

    def __init__(
        self,
        push_service: EnvironmentPushService,
        runtime_store: RuntimeCoordinationStore,
        policy_engine: PolicyEngine,
    ) -> None:
        self.push_service = push_service
        self.runtime_store = runtime_store
        self.policy_engine = policy_engine

    def apply_or_request(
        self,
        preview_id: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> EnvironmentPushOutcome:
        """Apply a matching grant or create one exact allow-once approval."""
        preview = self.push_service.get_preview(preview_id)
        completed = self.push_service.completed_result(preview.id)
        if completed is not None:
            return EnvironmentPushOutcome(
                preview=preview,
                result=completed,
                idempotent_replay=True,
            )
        self.push_service.validate_current(preview)
        recovered = self.push_service.completed_result(preview.id)
        if recovered is not None:
            return EnvironmentPushOutcome(
                preview=preview,
                result=recovered,
                idempotent_replay=True,
            )
        context = PolicyContext(
            project_id=project_id or preview.scope_id,
            session_id=session_id,
            reason="Push the exact reviewed commit to the exact remote branch.",
            preview=preview.to_dict(),
            approval_binding=preview.approval_binding,
            enforcement_owner=ENVIRONMENT_PUSH_OWNER,
        )
        resolution = self.policy_engine.resolve(
            PermissionAction.GIT_PUSH,
            profile=INTERACTIVE_PROFILE,
            context=context,
            enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        )
        if resolution.decision is PolicyDecision.DENY:
            raise EnvironmentPushError("policy_denied", "Push denied by policy.")
        if resolution.decision is PolicyDecision.ASK:
            approval = self.runtime_store.create_approval_request(resolution, context)
            return EnvironmentPushOutcome(preview=preview, approval=approval)
        result = self.push_service.apply(preview.id)
        return EnvironmentPushOutcome(preview=preview, result=result)
