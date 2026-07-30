"""Read-only GitHub environment enrichment service."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Callable

from .models import (
    EnvironmentSnapshot,
    HostedRepositoryHint,
)

from .github_contracts import (
    CommandRunner,
    GITHUB_CACHE_TTL_SECONDS,
    GITHUB_COMMAND_TIMEOUT_SECONDS,
    GITHUB_ENVIRONMENT_SCHEMA_VERSION,
    GITHUB_TOTAL_TIMEOUT_SECONDS,
    MAX_GITHUB_CHECKS,
    MAX_GITHUB_ISSUES,
    MAX_GITHUB_OUTPUT_BYTES,
    GitHubCountRollup,
    GitHubActionsRun,
    GitHubEnrichmentError,
    GitHubEnvironmentSnapshot,
    GitHubIssueState,
    GitHubPullRequestState,
    GitHubRepositoryIdentity,
    MAX_GITHUB_CACHE_ENTRIES,
    MAX_GITHUB_JOBS,
    MAX_GITHUB_RUNS,
    _BRANCH_RE,
    _CacheEntry,
    _CommandResult,
    _bounded_list,
    _classify_failure,
    _format_timestamp,
    _mapping,
    _parse_pull_request,
    _parse_repository,
    _parse_rollup,
    _parse_runs,
    _required_positive_int,
)

from .github_io import (
    _run_gh_command,
)

__all__ = [
    "CommandRunner",
    "GITHUB_CACHE_TTL_SECONDS",
    "GITHUB_COMMAND_TIMEOUT_SECONDS",
    "GITHUB_ENVIRONMENT_SCHEMA_VERSION",
    "GITHUB_TOTAL_TIMEOUT_SECONDS",
    "MAX_GITHUB_CACHE_ENTRIES",
    "MAX_GITHUB_CHECKS",
    "MAX_GITHUB_ISSUES",
    "MAX_GITHUB_JOBS",
    "MAX_GITHUB_OUTPUT_BYTES",
    "MAX_GITHUB_RUNS",
    "GitHubActionsRun",
    "GitHubCountRollup",
    "GitHubEnrichmentError",
    "GitHubEnvironmentService",
    "GitHubEnvironmentSnapshot",
    "GitHubIssueState",
    "GitHubPullRequestState",
    "GitHubRepositoryIdentity",
]


class GitHubEnvironmentService:
    """Inspect GitHub through explicit read-only gh commands and a bounded TTL."""

    def __init__(
        self,
        *,
        gh_executable: str | None = None,
        command_runner: CommandRunner | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        cache_ttl_seconds: float = GITHUB_CACHE_TTL_SECONDS,
    ) -> None:
        self._runner = command_runner or _run_gh_command
        executable = gh_executable or shutil.which("gh")
        if executable is not None and command_runner is None:
            candidate = Path(executable).expanduser().resolve()
            executable = (
                str(candidate)
                if candidate.is_file() and os.access(candidate, os.X_OK)
                else None
            )
        self._gh = executable
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._cache_ttl = max(0.0, min(float(cache_ttl_seconds), 300.0))
        self._cache: OrderedDict[tuple[str, ...], _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    def inspect(
        self,
        environment: EnvironmentSnapshot,
        repository_hint: HostedRepositoryHint | None,
        *,
        cancel_event: threading.Event | None = None,
        force: bool = False,
    ) -> GitHubEnvironmentSnapshot:
        """Return cached or freshly inspected GitHub state for one exact local HEAD."""
        cancel = cancel_event or threading.Event()
        checked_at = _format_timestamp(self._clock())
        if repository_hint is None:
            return _failure_snapshot("repository_unavailable", checked_at=checked_at)
        key = (
            repository_hint.host,
            repository_hint.name_with_owner,
            environment.worktree_root,
            environment.branch or "",
            environment.head or "",
        )
        cached = self._cached(key)
        if cached is not None and not force:
            return replace(cached, cached=True)
        if self._gh is None:
            return self._stale_or_failure(key, "gh_unavailable", checked_at)
        if (
            environment.branch is None
            or _BRANCH_RE.fullmatch(environment.branch) is None
        ):
            return self._stale_or_failure(key, "branch_unavailable", checked_at)
        if cancel.is_set():
            return self._stale_or_failure(key, "cancelled", checked_at)

        started = self._monotonic()
        repo_arg = f"{repository_hint.host}/{repository_hint.name_with_owner}"
        try:
            auth = self._command(
                environment,
                cancel,
                started,
                "auth",
                "status",
                "--active",
                "--hostname",
                repository_hint.host,
                allow_failure=True,
            )
            if auth.returncode != 0:
                code = _classify_failure(auth.stderr, default="unauthenticated")
                return self._stale_or_failure(
                    key, code, checked_at, auth_status="unauthenticated"
                )

            repo_payload = self._json_command(
                environment,
                cancel,
                started,
                "repo",
                "view",
                repo_arg,
                "--json",
                "nameWithOwner,url,isFork,defaultBranchRef",
            )
            repository = _parse_repository(repo_payload, repository_hint)
            pr_payload = self._json_command(
                environment,
                cancel,
                started,
                "pr",
                "list",
                "--repo",
                repo_arg,
                "--head",
                environment.branch,
                "--state",
                "all",
                "--limit",
                "1",
                "--json",
                "number,state,url,isDraft,headRefName,baseRefName,closingIssuesReferences,statusCheckRollup",
            )
            pull_request = _parse_pull_request(
                pr_payload,
                repository_hint.host,
                expected_branch=environment.branch,
            )
            runs_payload = self._json_command(
                environment,
                cancel,
                started,
                "run",
                "list",
                "--repo",
                repo_arg,
                "--branch",
                environment.branch,
                "--limit",
                str(MAX_GITHUB_RUNS),
                "--json",
                "databaseId,status,conclusion,headSha,url,createdAt,updatedAt",
            )
            run_items = _bounded_list(runs_payload, MAX_GITHUB_RUNS, "Actions runs")
            jobs = GitHubCountRollup()
            if run_items:
                run_id = _required_positive_int(run_items[0], "databaseId")
                jobs_payload = self._json_command(
                    environment,
                    cancel,
                    started,
                    "run",
                    "view",
                    str(run_id),
                    "--repo",
                    repo_arg,
                    "--json",
                    "jobs",
                )
                jobs = _parse_rollup(
                    _bounded_list(
                        _mapping(jobs_payload).get("jobs"),
                        MAX_GITHUB_JOBS,
                        "Actions jobs",
                    )
                )
            runs = _parse_runs(run_items, repository_hint.host, jobs)
        except GitHubEnrichmentError as exc:
            return self._stale_or_failure(key, exc.code, checked_at)

        snapshot = GitHubEnvironmentSnapshot(
            status="ready",
            auth_status="authenticated",
            checked_at=checked_at,
            repository=repository,
            pull_request=pull_request,
            runs=runs,
        )
        self._store(key, snapshot)
        return snapshot

    def _command(
        self,
        environment: EnvironmentSnapshot,
        cancel: threading.Event,
        started: float,
        *args: str,
        allow_failure: bool = False,
    ) -> _CommandResult:
        remaining = GITHUB_TOTAL_TIMEOUT_SECONDS - (self._monotonic() - started)
        if remaining <= 0:
            raise GitHubEnrichmentError(
                "github_timeout", "GitHub enrichment timed out."
            )
        result = self._runner(
            (str(self._gh), *args),
            Path(environment.worktree_root),
            min(GITHUB_COMMAND_TIMEOUT_SECONDS, remaining),
            cancel,
        )
        if result.returncode != 0 and not allow_failure:
            raise GitHubEnrichmentError(
                _classify_failure(result.stderr), "GitHub inspection failed."
            )
        return result

    def _json_command(
        self,
        environment: EnvironmentSnapshot,
        cancel: threading.Event,
        started: float,
        *args: str,
    ) -> Any:
        result = self._command(environment, cancel, started, *args)
        try:
            return json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubEnrichmentError(
                "github_output_invalid", "GitHub returned invalid JSON."
            ) from exc

    def _cached(self, key: tuple[str, ...]) -> GitHubEnvironmentSnapshot | None:
        now = self._monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None or now - entry.stored_at > self._cache_ttl:
                return None
            self._cache.move_to_end(key)
            return entry.snapshot

    def _store(self, key: tuple[str, ...], snapshot: GitHubEnvironmentSnapshot) -> None:
        with self._lock:
            self._cache[key] = _CacheEntry(self._monotonic(), snapshot)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_GITHUB_CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def _stale_or_failure(
        self,
        key: tuple[str, ...],
        code: str,
        checked_at: str,
        *,
        auth_status: str = "unknown",
    ) -> GitHubEnvironmentSnapshot:
        with self._lock:
            entry = self._cache.get(key)
        if entry is not None:
            return replace(
                entry.snapshot,
                status="stale",
                reason_code=code,
                cached=True,
                stale=True,
            )
        return _failure_snapshot(code, checked_at=checked_at, auth_status=auth_status)


def _failure_snapshot(
    code: str,
    *,
    checked_at: str,
    auth_status: str = "unknown",
) -> GitHubEnvironmentSnapshot:
    status = (
        "rate_limited"
        if code == "rate_limited"
        else "cancelled"
        if code == "cancelled"
        else "unavailable"
    )
    return GitHubEnvironmentSnapshot(
        status=status,
        auth_status=auth_status,
        checked_at=checked_at,
        reason_code=code,
        stale=False,
    )
