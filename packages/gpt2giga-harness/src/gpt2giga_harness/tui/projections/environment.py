"""Typed environment projections for TUI clients."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence


from gpt2giga_harness.environments import (
    EnvironmentCaptureError,
    EnvironmentSnapshot,
    GitEnvironmentProvider,
)
from gpt2giga_harness.github_environments import (
    GitHubEnvironmentService,
    GitHubEnvironmentSnapshot,
)
from gpt2giga_harness.runtime.policy import approval_request_to_dict

from gpt2giga_harness.tui.contracts import (
    EnvironmentSummary,
    EnvironmentCommitPreviewSummary,
    EnvironmentCommitApplySummary,
    EnvironmentPushPreviewSummary,
    EnvironmentPushApplySummary,
    EnvironmentPullRequestPreviewSummary,
    EnvironmentPullRequestApplySummary,
)

from gpt2giga_harness.tui.projections.values import (
    _mapping,
    _mapping_items,
    _optional_text,
    _display_text,
    _required_identity,
)


from gpt2giga_harness.tui.projections.runs import _approval_summary


def _capture_environment_summary(
    workspace: str,
    github_service: GitHubEnvironmentService | None = None,
) -> EnvironmentSummary:
    try:
        provider = GitEnvironmentProvider()
        snapshot = provider.snapshot(workspace)
        summary = _environment_summary_from_snapshot(snapshot)
        if github_service is None:
            return summary
        github = github_service.inspect(snapshot, provider.hosted_repository(snapshot))
        return _environment_summary_with_github(summary, github)
    except EnvironmentCaptureError as exc:
        return EnvironmentSummary("unavailable", reason=str(exc))


def _environment_summary_from_snapshot(
    snapshot: EnvironmentSnapshot,
) -> EnvironmentSummary:
    return EnvironmentSummary(
        status="fresh",
        branch=snapshot.branch,
        detached=snapshot.detached,
        head=snapshot.head,
        worktree_root=snapshot.worktree_root,
        staged_count=snapshot.staged_count,
        unstaged_count=snapshot.unstaged_count,
        untracked_count=snapshot.untracked_count,
        additions=snapshot.additions,
        deletions=snapshot.deletions,
        commit_ready=snapshot.staged_count > 0,
        push_ready=snapshot.push_ready,
        push_blocker=snapshot.push_blocker,
        captured_at=snapshot.captured_at,
    )


def _environment_summary_from_mapping(data: Mapping[str, Any]) -> EnvironmentSummary:
    snapshot = EnvironmentSnapshot.from_dict(_mapping(data.get("environment")))
    summary = _environment_summary_from_snapshot(snapshot)
    freshness = _mapping(data.get("freshness"))
    issue_pr = _mapping(data.get("issue_pr"))
    commit = _mapping(data.get("commit"))
    github = _mapping(data.get("github"))
    repository = _mapping(github.get("repository"))
    pull_request = _mapping(github.get("pull_request"))
    checks = _mapping(pull_request.get("checks"))
    runs = _mapping_items(github.get("runs"), 5)
    issue_status = str(issue_pr.get("status") or "not_connected")
    number = issue_pr.get("number")
    if issue_pr.get("kind") == "pull_request" and isinstance(number, int):
        issue_status = f"PR #{number} {issue_status} · checks {checks.get('status') or 'unavailable'}"
    return replace(
        summary,
        status=str(freshness.get("status") or "stale"),
        commit_ready=bool(commit.get("ready")),
        issue_pr_status=issue_status,
        github_status=str(github.get("status") or "unavailable"),
        github_repository=_optional_text(repository.get("name_with_owner")),
        github_checks=str(checks.get("status") or "unavailable"),
        github_actions=_github_actions_status(runs),
        github_run_count=len(runs),
        github_checked_at=_optional_text(github.get("checked_at")),
    )


def _environment_summary_with_github(
    summary: EnvironmentSummary,
    github: GitHubEnvironmentSnapshot,
) -> EnvironmentSummary:
    pull_request = github.pull_request
    issue_pr = "none" if github.status == "ready" else "not_connected"
    checks = "unavailable"
    if pull_request is not None:
        checks = pull_request.checks.status
        issue_pr = f"PR #{pull_request.number} {pull_request.state} · checks {checks}"
    runs = tuple(run.to_dict() for run in github.runs)
    return replace(
        summary,
        issue_pr_status=issue_pr,
        github_status=github.status,
        github_repository=(
            github.repository.name_with_owner if github.repository else None
        ),
        github_checks=checks,
        github_actions=_github_actions_status(runs),
        github_run_count=len(runs),
        github_checked_at=github.checked_at,
    )


def _github_actions_status(runs: Sequence[Mapping[str, Any]]) -> str:
    if not runs:
        return "unavailable"
    latest = runs[0]
    return str(latest.get("conclusion") or latest.get("status") or "unknown")


def _environment_commit_preview_summary(
    value: Mapping[str, Any],
) -> EnvironmentCommitPreviewSummary:
    author = _mapping(value.get("author"))
    return EnvironmentCommitPreviewSummary(
        id=_required_identity(value.get("id"), "commit preview id"),
        branch=_display_text(value.get("branch") or "detached"),
        head=_optional_text(value.get("head")),
        diff_sha256=_required_identity(value.get("diff_sha256"), "diff hash"),
        staged_count=max(0, int(value.get("staged_count", 0))),
        message=_display_text(value.get("message") or ""),
        author_name=_display_text(author.get("name") or ""),
        author_email=_display_text(author.get("email") or ""),
        worktree_root=_display_text(value.get("worktree_root") or ""),
    )


def _environment_commit_apply_summary(
    value: Mapping[str, Any],
) -> EnvironmentCommitApplySummary:
    preview = _environment_commit_preview_summary(_mapping(value.get("preview")))
    approval_payload = value.get("approval")
    result = _mapping(value.get("result"))
    return EnvironmentCommitApplySummary(
        preview=preview,
        approval=(
            _approval_summary(approval_payload)
            if isinstance(approval_payload, Mapping)
            else None
        ),
        commit_head=_optional_text(result.get("commit_head")),
        idempotent_replay=bool(value.get("idempotent_replay", False)),
    )


def _environment_commit_outcome_summary(outcome: Any) -> EnvironmentCommitApplySummary:
    return EnvironmentCommitApplySummary(
        preview=_environment_commit_preview_summary(outcome.preview.to_dict()),
        approval=(
            _approval_summary(approval_request_to_dict(outcome.approval))
            if outcome.approval is not None
            else None
        ),
        commit_head=(
            outcome.result.commit_head if outcome.result is not None else None
        ),
        idempotent_replay=outcome.idempotent_replay,
    )


def _environment_push_preview_summary(
    value: Mapping[str, Any],
) -> EnvironmentPushPreviewSummary:
    repository = _mapping(value.get("repository"))
    return EnvironmentPushPreviewSummary(
        id=_required_identity(value.get("id"), "push preview id"),
        branch=_display_text(value.get("branch") or "detached"),
        head=_required_identity(value.get("head"), "push head"),
        diff_sha256=_required_identity(value.get("diff_sha256"), "diff hash"),
        remote=_display_text(value.get("remote") or "unavailable"),
        upstream=_optional_text(value.get("upstream")),
        target_branch=_display_text(value.get("target_branch") or "unavailable"),
        remote_head=_optional_text(value.get("remote_head")),
        repository=_display_text(repository.get("name_with_owner") or "unavailable"),
        worktree_root=_display_text(value.get("worktree_root") or ""),
    )


def _environment_push_apply_summary(
    value: Mapping[str, Any],
) -> EnvironmentPushApplySummary:
    preview = _environment_push_preview_summary(_mapping(value.get("preview")))
    approval_payload = value.get("approval")
    result = _mapping(value.get("result"))
    return EnvironmentPushApplySummary(
        preview=preview,
        approval=(
            _approval_summary(approval_payload)
            if isinstance(approval_payload, Mapping)
            else None
        ),
        commit_head=_optional_text(result.get("commit_head")),
        remote_commit_url=_optional_text(result.get("remote_commit_url")),
        run_evidence_url=_optional_text(result.get("run_evidence_url")),
        idempotent_replay=bool(value.get("idempotent_replay", False)),
    )


def _environment_push_outcome_summary(outcome: Any) -> EnvironmentPushApplySummary:
    return EnvironmentPushApplySummary(
        preview=_environment_push_preview_summary(outcome.preview.to_dict()),
        approval=(
            _approval_summary(approval_request_to_dict(outcome.approval))
            if outcome.approval is not None
            else None
        ),
        commit_head=(
            outcome.result.commit_head if outcome.result is not None else None
        ),
        remote_commit_url=(
            outcome.result.remote_commit_url if outcome.result is not None else None
        ),
        run_evidence_url=(
            outcome.result.run_evidence_url if outcome.result is not None else None
        ),
        idempotent_replay=outcome.idempotent_replay,
    )


def _environment_pull_request_preview_summary(
    value: Mapping[str, Any],
) -> EnvironmentPullRequestPreviewSummary:
    repository = _mapping(value.get("repository"))
    return EnvironmentPullRequestPreviewSummary(
        id=_required_identity(value.get("id"), "pull-request preview id"),
        repository=_display_text(repository.get("name_with_owner") or "unavailable"),
        remote=_display_text(value.get("remote") or "unavailable"),
        source_branch=_display_text(value.get("source_branch") or "unavailable"),
        source_head=_required_identity(value.get("source_head"), "source head"),
        source_remote_head=_required_identity(
            value.get("source_remote_head"), "remote source head"
        ),
        base_branch=_display_text(value.get("base_branch") or "unavailable"),
        base_head=_required_identity(value.get("base_head"), "base head"),
        diff_sha256=_required_identity(value.get("diff_sha256"), "diff hash"),
        title=_display_text(value.get("title") or ""),
        body=_display_text(value.get("body") or ""),
        worktree_root=_display_text(value.get("worktree_root") or ""),
    )


def _environment_pull_request_apply_summary(
    value: Mapping[str, Any],
) -> EnvironmentPullRequestApplySummary:
    preview = _environment_pull_request_preview_summary(_mapping(value.get("preview")))
    approval_payload = value.get("approval")
    result = _mapping(value.get("result"))
    number = result.get("number")
    return EnvironmentPullRequestApplySummary(
        preview=preview,
        approval=(
            _approval_summary(approval_payload)
            if isinstance(approval_payload, Mapping)
            else None
        ),
        number=(
            number if isinstance(number, int) and not isinstance(number, bool) else None
        ),
        commit_head=_optional_text(result.get("commit_head")),
        pull_request_url=_optional_text(result.get("pull_request_url")),
        commit_url=_optional_text(result.get("commit_url")),
        checks_url=_optional_text(result.get("checks_url")),
        run_evidence_url=_optional_text(result.get("run_evidence_url")),
        idempotent_replay=bool(value.get("idempotent_replay", False)),
    )


def _environment_pull_request_outcome_summary(
    outcome: Any,
) -> EnvironmentPullRequestApplySummary:
    result = outcome.result
    return EnvironmentPullRequestApplySummary(
        preview=_environment_pull_request_preview_summary(outcome.preview.to_dict()),
        approval=(
            _approval_summary(approval_request_to_dict(outcome.approval))
            if outcome.approval is not None
            else None
        ),
        number=result.number if result is not None else None,
        commit_head=result.commit_head if result is not None else None,
        pull_request_url=result.pull_request_url if result is not None else None,
        commit_url=result.commit_url if result is not None else None,
        checks_url=result.checks_url if result is not None else None,
        run_evidence_url=result.run_evidence_url if result is not None else None,
        idempotent_replay=outcome.idempotent_replay,
    )
