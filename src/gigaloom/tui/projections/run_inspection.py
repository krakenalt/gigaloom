"""Typed run inspection projections for TUI clients."""

from __future__ import annotations

from typing import Any, Mapping


from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.sessions.models import (
    HarnessRun,
)
from gigaloom.worktrees import run_diff_response

from gigaloom.tui.contracts import (
    MAX_DIFF_PREVIEW_CHARS,
    ArtifactSummary,
    RunInspection,
)

from gigaloom.tui.projections.values import (
    _safe_paths,
    _optional_non_negative_int,
    _bounded_content_text,
    _mapping,
    _mapping_items,
    _required_text,
    _display_text,
    _required_identity,
)


from gigaloom.tui.projections.runs import _run_revision, _mapping_revision


def _in_process_run_inspection(
    run: HarnessRun,
    *,
    registry: HarnessRegistry,
    runtime_store: RuntimeCoordinationStore,
) -> RunInspection:
    diff = run_diff_response(run.metadata)
    patch = _bounded_content_text(diff.get("patch") or "", MAX_DIFF_PREVIEW_CHARS)
    raw_patch = str(diff.get("patch") or "")
    metadata = _mapping(run.metadata)
    execution = _mapping(metadata.get("workspace_execution"))
    artifacts: list[ArtifactSummary] = []
    if raw_patch:
        artifacts.append(ArtifactSummary("diff", len(raw_patch.encode("utf-8"))))
    if execution.get("worktree_path"):
        artifacts.append(ArtifactSummary("worktree", None))
    if isinstance(metadata.get("pr_artifact"), Mapping):
        artifacts.append(ArtifactSummary("pr_report", None))
    link = _mapping(metadata.get("structured_session_link"))
    continuity = (
        f"provider link revision {link.get('revision', 'unknown')}"
        if link
        else "no provider session link retained"
    )
    recovery = _display_text(link.get("recovery_state") or "not_required")
    try:
        availability = registry.get(run.harness_id).availability().status.value
    except (AttributeError, KeyError, ValueError):
        availability = "unknown"
    job = runtime_store.find_job_for_run(run.id)
    attempts = runtime_store.list_attempts(job.id) if job is not None else ()
    evidence = (
        f"run={run.id} session={run.session_id}",
        f"status={run.status.value} harness={run.harness_id} availability={availability}",
        (
            f"durable_job={job.status.value} attempts={len(attempts)}"
            if job is not None
            else "durable_job=not_present"
        ),
        f"artifacts={','.join(item.type for item in artifacts) or 'none'}",
        "environment=deferred_to_N6",
    )
    return RunInspection(
        run_id=run.id,
        status=run.status.value,
        revision=_run_revision(run),
        provider_continuity=continuity,
        harness_status=_display_text(availability),
        recovery=recovery,
        artifacts=tuple(artifacts),
        diff=patch,
        diff_truncated=len(raw_patch) > len(patch),
        changed_files=_safe_paths(diff.get("changed_files")),
        untracked_files=_safe_paths(diff.get("untracked_files")),
        evidence=evidence,
    )


def _attached_run_inspection(
    run_payload: Mapping[str, Any],
    diff_payload: Mapping[str, Any],
    summary_payload: Mapping[str, Any],
    harness_payload: Mapping[str, Any],
) -> RunInspection:
    run = _mapping(run_payload.get("run"))
    diff_projection = _mapping(diff_payload.get("patch"))
    provider_session = _mapping(run.get("provider_session"))
    artifacts = tuple(
        ArtifactSummary(
            _display_text(item.get("type") or "artifact"),
            _optional_non_negative_int(item.get("byte_count")),
        )
        for item in _mapping_items(run.get("artifacts"), 50)
    )
    summary = _mapping(summary_payload.get("run"))
    job = _mapping(summary.get("job"))
    explanations = _mapping_items(summary.get("explanations"), 20)
    recovery_item = next(
        (
            item
            for item in explanations
            if str(item.get("id") or item.get("kind") or "") == "recovery"
        ),
        {},
    )
    recovery = _display_text(
        recovery_item.get("summary")
        or provider_session.get("recovery_state")
        or "not_required"
    )
    continuity = (
        f"provider link revision {provider_session.get('revision', 'unknown')}"
        if provider_session
        else "no provider session link retained"
    )
    run_id = _required_identity(run.get("id"), "run id")
    status = _display_text(run.get("status") or "unknown")
    harness_id = _display_text(run.get("harness_id") or "unknown")
    harness_status = "unknown"
    for item in _mapping_items(harness_payload.get("harnesses"), 100):
        if str(_mapping(item.get("spec")).get("id") or "") == harness_id:
            harness_status = _display_text(
                _mapping(item.get("availability")).get("status") or "unknown"
            )
            break
    evidence = (
        f"run={run_id} session={_display_text(run.get('session_id') or 'unknown')}",
        f"status={status} harness={harness_id}",
        (
            f"durable_job={_display_text(job.get('status'))}"
            if job
            else "durable_job=not_present"
        ),
        f"artifacts={','.join(item.type for item in artifacts) or 'none'}",
        "environment=deferred_to_N6",
    )
    patch = _bounded_content_text(
        diff_projection.get("text") or "", MAX_DIFF_PREVIEW_CHARS
    )
    return RunInspection(
        run_id=run_id,
        status=status,
        revision=_required_text(
            run_payload.get("snapshot_revision") or _mapping_revision(run),
            "run revision",
        ),
        provider_continuity=continuity,
        harness_status=f"{harness_id} [{harness_status}]",
        recovery=recovery,
        artifacts=artifacts,
        diff=patch,
        diff_truncated=bool(diff_projection.get("truncated")),
        changed_files=_safe_paths(diff_payload.get("changed_files")),
        untracked_files=_safe_paths(diff_payload.get("untracked_files")),
        evidence=evidence,
    )
