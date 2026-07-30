"""Governed environment pull request service."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Mapping
from urllib.parse import quote

from .models import (
    EnvironmentCaptureError,
    EnvironmentSnapshot,
    HostedRepositoryHint,
)
from .capture import GitEnvironmentProvider

from .pull_request_contracts import (
    ENVIRONMENT_PULL_REQUEST_OWNER,
    ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION,
    EnvironmentPullRequestError,
    EnvironmentPullRequestPreview,
    EnvironmentPullRequestResult,
    HOSTED_COMMAND_TIMEOUT_SECONDS,
    HostedCommandRunner,
    MAX_HOSTED_OUTPUT_BYTES,
    MAX_PULL_REQUEST_BODY_CHARS,
    MAX_PULL_REQUEST_TITLE_CHARS,
    _CommandResult,
    _HEX_SHA_RE,
    _classify_hosted_failure,
    _mapping_hash,
    _parse_pull_request,
    _parse_pull_request_mapping,
    _safe_repository_url,
    _snapshot_matches_preview,
    _utc_now,
    _validate_body,
    _validate_preview_id,
    _validate_ref_component,
    _validate_title,
    _write_private_json,
)

from .pull_request_io import (
    _noninteractive_environment,
    _resolve_executable,
    _run_hosted_command,
)


class EnvironmentPullRequestService:
    """Preview and create exact same-repository pull requests through gh."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        git_executable: str | None = None,
        gh_executable: str | None = None,
        command_runner: HostedCommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        repository_resolver: Callable[
            [EnvironmentSnapshot], HostedRepositoryHint | None
        ]
        | None = None,
    ) -> None:
        git = _resolve_executable(git_executable or shutil.which("git"))
        if git is None:
            raise EnvironmentPullRequestError("git_unavailable", "Git is unavailable.")
        gh = gh_executable or shutil.which("gh")
        if command_runner is None:
            gh = _resolve_executable(gh)
        if gh is None:
            raise EnvironmentPullRequestError(
                "gh_unavailable", "GitHub CLI is unavailable."
            )
        self._git = git
        self._gh = str(gh)
        self._runner = command_runner or _run_hosted_command
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._provider = GitEnvironmentProvider(git_executable=git, clock=self._clock)
        self._repository_resolver = (
            repository_resolver or self._provider.hosted_repository
        )
        self._root = Path(data_dir).expanduser().resolve() / "environment_pull_requests"
        self._previews = self._root / "previews"
        self._results = self._root / "results"
        self._lock = threading.RLock()

    def preview(
        self,
        workspace: str | Path,
        *,
        title: str,
        body: str,
        base_branch: str | None = None,
    ) -> EnvironmentPullRequestPreview:
        """Persist or reuse one local/remote/hosted-state-bound PR preview."""
        title = _validate_title(title)
        body = _validate_body(body)
        snapshot = self._capture(workspace)
        remote, source_branch, source_head = self._source(snapshot)
        hint = self._repository_resolver(snapshot)
        if hint is None:
            raise EnvironmentPullRequestError(
                "hosted_remote_required",
                "A governed pull request requires one supported hosted remote.",
            )
        source_remote_head = self._remote_head(
            Path(snapshot.worktree_root), remote, source_branch
        )
        if source_remote_head != source_head:
            raise EnvironmentPullRequestError(
                "source_not_pushed",
                "The exact source HEAD must be pushed before pull-request creation.",
            )
        repository_url, default_branch = self._repository(snapshot, hint)
        base = _validate_ref_component(base_branch or default_branch, "base branch")
        if base == source_branch:
            raise EnvironmentPullRequestError(
                "base_matches_source",
                "Pull-request base and source branches must differ.",
            )
        base_head = self._remote_head(Path(snapshot.worktree_root), remote, base)
        if base_head is None:
            raise EnvironmentPullRequestError(
                "base_unavailable", "The pull-request base branch is unavailable."
            )
        semantic = {
            "schema_version": ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION,
            "repository_root": snapshot.repository_root,
            "worktree_root": snapshot.worktree_root,
            "repository_host": hint.host,
            "repository_name": hint.name_with_owner,
            "repository_url": repository_url,
            "remote": remote,
            "source_branch": source_branch,
            "source_head": source_head,
            "source_remote_head": source_remote_head,
            "base_branch": base,
            "base_head": base_head,
            "diff_sha256": snapshot.diff_sha256,
            "title": title,
            "body": body,
        }
        preview_id = f"pull_request_{_mapping_hash(semantic)}"
        path = self._preview_path(preview_id)
        with self._lock:
            if path.is_file():
                return self.get_preview(preview_id)
            preview = EnvironmentPullRequestPreview(
                id=preview_id,
                repository_root=snapshot.repository_root,
                worktree_root=snapshot.worktree_root,
                repository_host=hint.host,
                repository_name=hint.name_with_owner,
                repository_url=repository_url,
                remote=remote,
                source_branch=source_branch,
                source_head=source_head,
                source_remote_head=source_remote_head,
                base_branch=base,
                base_head=base_head,
                diff_sha256=snapshot.diff_sha256,
                title=title,
                body=body,
                created_at=_utc_now(self._clock),
            )
            _write_private_json(path, preview.to_dict())
            return preview

    def get_preview(self, preview_id: str) -> EnvironmentPullRequestPreview:
        """Load one exact private pull-request preview."""
        path = self._preview_path(preview_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("pull-request preview record is invalid")
            return EnvironmentPullRequestPreview.from_dict(payload)
        except FileNotFoundError as exc:
            raise EnvironmentPullRequestError(
                "preview_not_found", "Pull-request preview was not found."
            ) from exc
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EnvironmentPullRequestError(
                "preview_invalid", "Pull-request preview is unavailable."
            ) from exc

    def completed_result(self, preview_id: str) -> EnvironmentPullRequestResult | None:
        """Return durable completion evidence without contacting GitHub."""
        path = self._result_path(preview_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("pull-request result record is invalid")
            return EnvironmentPullRequestResult.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise EnvironmentPullRequestError(
                "result_invalid", "Pull-request completion evidence is unavailable."
            ) from exc

    def validate_current(self, preview: EnvironmentPullRequestPreview) -> None:
        """Fail before approval consumption when any bound state is stale."""
        if self.completed_result(preview.id) is not None:
            return
        snapshot = self._capture(preview.worktree_root)
        if not _snapshot_matches_preview(snapshot, preview):
            raise EnvironmentPullRequestError(
                "stale_preview", "Git state changed after the pull-request preview."
            )
        hint = self._repository_resolver(snapshot)
        if (
            hint is None
            or hint.host != preview.repository_host
            or hint.name_with_owner.casefold() != preview.repository_name.casefold()
        ):
            raise EnvironmentPullRequestError(
                "remote_changed", "Hosted repository changed after the preview."
            )
        repository_url, _ = self._repository(snapshot, hint)
        if repository_url != preview.repository_url:
            raise EnvironmentPullRequestError(
                "remote_changed", "Hosted repository changed after the preview."
            )
        root = Path(preview.worktree_root)
        if (
            self._remote_head(root, preview.remote, preview.source_branch)
            != preview.source_remote_head
        ):
            raise EnvironmentPullRequestError(
                "remote_changed", "Source branch changed after the preview."
            )
        if (
            self._remote_head(root, preview.remote, preview.base_branch)
            != preview.base_head
        ):
            raise EnvironmentPullRequestError(
                "remote_changed", "Base branch changed after the preview."
            )

    def apply(self, preview_id: str) -> EnvironmentPullRequestResult:
        """Create one exact PR once, or recover the matching hosted result."""
        with self._lock:
            preview = self.get_preview(preview_id)
            completed = self.completed_result(preview.id)
            if completed is not None:
                return completed
            self.validate_current(preview)
            existing = self._find_existing(preview)
            if existing is not None:
                result = self._result(preview, existing, recovered=True)
                _write_private_json(self._result_path(preview.id), result.to_dict())
                return result
            payload = json.dumps(
                {
                    "title": preview.title,
                    "body": preview.body,
                    "head": preview.source_branch,
                    "base": preview.base_branch,
                    "draft": False,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            response = self._command(
                Path(preview.worktree_root),
                "api",
                "--hostname",
                preview.repository_host,
                "--method",
                "POST",
                f"repos/{preview.repository_name}/pulls",
                "--input",
                "-",
                input_bytes=payload,
                allow_failure=True,
            )
            if response.returncode != 0:
                existing = self._find_existing(preview)
                if existing is None:
                    raise EnvironmentPullRequestError(
                        _classify_hosted_failure(response.stderr),
                        "Pull-request creation failed.",
                    )
                result = self._result(preview, existing, recovered=True)
            else:
                result = self._result(
                    preview,
                    _parse_pull_request(response.stdout, preview),
                    recovered=False,
                )
            _write_private_json(self._result_path(preview.id), result.to_dict())
            return result

    def _capture(self, workspace: str | Path) -> EnvironmentSnapshot:
        try:
            return self._provider.snapshot(workspace)
        except EnvironmentCaptureError as exc:
            raise EnvironmentPullRequestError(exc.code, str(exc)) from exc

    def _source(self, snapshot: EnvironmentSnapshot) -> tuple[str, str, str]:
        if snapshot.detached or snapshot.branch is None:
            raise EnvironmentPullRequestError(
                "detached_head", "A governed pull request requires an attached branch."
            )
        if snapshot.head is None:
            raise EnvironmentPullRequestError(
                "unborn_head", "A governed pull request requires a commit."
            )
        if snapshot.remote is None:
            raise EnvironmentPullRequestError(
                "remote_unavailable", "A governed pull request requires a remote."
            )
        remote = _validate_ref_component(snapshot.remote, "remote")
        source_branch = snapshot.branch
        if snapshot.upstream is not None:
            prefix = f"{remote}/"
            if not snapshot.upstream.startswith(prefix):
                raise EnvironmentPullRequestError(
                    "upstream_mismatch",
                    "The selected remote does not own the upstream.",
                )
            source_branch = snapshot.upstream.removeprefix(prefix)
        return (
            remote,
            _validate_ref_component(source_branch, "source branch"),
            snapshot.head,
        )

    def _repository(
        self, snapshot: EnvironmentSnapshot, hint: HostedRepositoryHint
    ) -> tuple[str, str]:
        self._auth(snapshot, hint.host)
        payload = self._json_command(
            Path(snapshot.worktree_root),
            "repo",
            "view",
            f"{hint.host}/{hint.name_with_owner}",
            "--json",
            "nameWithOwner,url,defaultBranchRef,isFork",
        )
        if not isinstance(payload, Mapping):
            raise EnvironmentPullRequestError(
                "hosted_output_invalid", "Hosted repository output is invalid."
            )
        name = str(payload.get("nameWithOwner", ""))
        if name.casefold() != hint.name_with_owner.casefold():
            raise EnvironmentPullRequestError(
                "repository_mismatch", "Hosted repository identity changed."
            )
        if payload.get("isFork") is True:
            raise EnvironmentPullRequestError(
                "fork_unsupported", "Cross-repository pull requests require review."
            )
        if payload.get("isFork") is not False:
            raise EnvironmentPullRequestError(
                "hosted_output_invalid", "Hosted repository fork state is invalid."
            )
        url = _safe_repository_url(str(payload.get("url", "")), hint)
        default = payload.get("defaultBranchRef")
        if not isinstance(default, Mapping):
            raise EnvironmentPullRequestError(
                "base_unavailable", "The default base branch is unavailable."
            )
        return url, _validate_ref_component(str(default.get("name", "")), "base branch")

    def _auth(self, snapshot: EnvironmentSnapshot, host: str) -> None:
        result = self._command(
            Path(snapshot.worktree_root),
            "auth",
            "status",
            "--active",
            "--hostname",
            host,
            allow_failure=True,
        )
        if result.returncode != 0:
            raise EnvironmentPullRequestError(
                _classify_hosted_failure(result.stderr, default="unauthenticated"),
                "Hosted authentication is unavailable.",
            )

    def _find_existing(
        self, preview: EnvironmentPullRequestPreview
    ) -> Mapping[str, Any] | None:
        payload = self._json_command(
            Path(preview.worktree_root),
            "pr",
            "list",
            "--repo",
            f"{preview.repository_host}/{preview.repository_name}",
            "--head",
            preview.source_branch,
            "--base",
            preview.base_branch,
            "--state",
            "open",
            "--limit",
            "10",
            "--json",
            "number,url,state,headRefName,headRefOid,baseRefName",
        )
        if not isinstance(payload, list) or len(payload) > 10:
            raise EnvironmentPullRequestError(
                "hosted_output_invalid", "Pull-request lookup output is invalid."
            )
        matches = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise EnvironmentPullRequestError(
                    "hosted_output_invalid", "Pull-request lookup output is invalid."
                )
            if (
                item.get("headRefName") == preview.source_branch
                and item.get("headRefOid") == preview.source_head
                and item.get("baseRefName") == preview.base_branch
            ):
                matches.append(item)
        if len(matches) > 1:
            raise EnvironmentPullRequestError(
                "pull_request_ambiguous", "Matching pull-request state is ambiguous."
            )
        return matches[0] if matches else None

    def _remote_head(self, root: Path, remote: str, branch: str) -> str | None:
        environment = _noninteractive_environment()
        result = _run_hosted_command(
            (
                self._git,
                "-c",
                "credential.helper=",
                "ls-remote",
                "--refs",
                "--heads",
                "--exit-code",
                "--",
                remote,
                f"refs/heads/{branch}",
            ),
            root,
            None,
            HOSTED_COMMAND_TIMEOUT_SECONDS,
            environment=environment,
        )
        if result.returncode == 2 and not result.stdout.strip():
            return None
        if result.returncode != 0:
            raise EnvironmentPullRequestError(
                "remote_unavailable", "Remote Git state is unavailable."
            )
        records = [
            line.split()
            for line in result.stdout.decode("ascii", "strict").splitlines()
            if line
        ]
        if (
            len(records) != 1
            or len(records[0]) != 2
            or _HEX_SHA_RE.fullmatch(records[0][0]) is None
        ):
            raise EnvironmentPullRequestError(
                "remote_output_invalid", "Remote Git state is invalid."
            )
        return records[0][0]

    def _json_command(self, root: Path, *args: str) -> Any:
        result = self._command(root, *args)
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EnvironmentPullRequestError(
                "hosted_output_invalid", "Hosted command returned invalid JSON."
            ) from exc

    def _command(
        self,
        root: Path,
        *args: str,
        input_bytes: bytes | None = None,
        allow_failure: bool = False,
    ) -> _CommandResult:
        result = self._runner(
            (self._gh, *args), root, input_bytes, HOSTED_COMMAND_TIMEOUT_SECONDS
        )
        if result.returncode != 0 and not allow_failure:
            raise EnvironmentPullRequestError(
                _classify_hosted_failure(result.stderr), "Hosted command failed."
            )
        return result

    def _result(
        self,
        preview: EnvironmentPullRequestPreview,
        payload: Mapping[str, Any],
        *,
        recovered: bool,
    ) -> EnvironmentPullRequestResult:
        parsed = _parse_pull_request_mapping(payload, preview)
        number = parsed["number"]
        url = parsed["url"]
        return EnvironmentPullRequestResult(
            preview_id=preview.id,
            number=number,
            state=parsed["state"],
            source_branch=preview.source_branch,
            base_branch=preview.base_branch,
            commit_head=preview.source_head,
            pull_request_url=url,
            commit_url=f"{preview.repository_url}/commit/{preview.source_head}",
            checks_url=f"{url}/checks",
            run_evidence_url=(
                f"{preview.repository_url}/actions?query="
                f"branch%3A{quote(preview.source_branch, safe='')}"
            ),
            completed_at=_utc_now(self._clock),
            recovered=recovered,
        )

    def _preview_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._previews / f"{preview_id}.json"

    def _result_path(self, preview_id: str) -> Path:
        _validate_preview_id(preview_id)
        return self._results / f"{preview_id}.json"


from .pull_request_governed import (  # noqa: E402
    EnvironmentPullRequestOutcome,
    GovernedEnvironmentPullRequestService,
)

__all__ = [
    "ENVIRONMENT_PULL_REQUEST_OWNER",
    "ENVIRONMENT_PULL_REQUEST_SCHEMA_VERSION",
    "HOSTED_COMMAND_TIMEOUT_SECONDS",
    "MAX_HOSTED_OUTPUT_BYTES",
    "MAX_PULL_REQUEST_BODY_CHARS",
    "MAX_PULL_REQUEST_TITLE_CHARS",
    "EnvironmentPullRequestError",
    "EnvironmentPullRequestOutcome",
    "EnvironmentPullRequestPreview",
    "EnvironmentPullRequestResult",
    "EnvironmentPullRequestService",
    "GovernedEnvironmentPullRequestService",
    "HostedCommandRunner",
]
