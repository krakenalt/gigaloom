"""Review preparation primitives."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from gpt2giga_harness.review.provenance import build_replay_request
from gpt2giga_harness.review.ports import (
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessStoredEvent,
)
from .codec import (
    _json_sha256,
    _mapping,
    _reject_unknown_fields,
    _required_target,
    _trace_replay_axis,
)
from .dimensions import (
    _execution_dimensions,
    _fixed_dimensions,
    _target_dimensions,
    _task_sha256,
    _unchanged_dimensions,
    trace_evidence_sha256,
)
from .models import (
    TRACE_REPLAY_SCHEMA_VERSION,
    TraceReplayAxis,
    TraceReplayManifest,
    _ACTIVE_STATUSES,
    _TRACE_REPLAY_FIELDS,
)


def prepare_trace_replay(
    source_run: HarnessRun,
    *,
    raw_request: HarnessRawRecord | None,
    payload: Mapping[str, Any],
    source_messages: Sequence[HarnessMessage] = (),
    source_events: Sequence[HarnessStoredEvent] = (),
    target_extension: Mapping[str, Any] | None = None,
    created_at: str,
) -> tuple[TraceReplayManifest, dict[str, Any]]:
    """Build one strict replay manifest and its existing-runner request."""
    _reject_unknown_fields(payload, _TRACE_REPLAY_FIELDS)
    if source_run.status.value in _ACTIVE_STATUSES:
        raise ValueError("trace replay requires a terminal source run")
    axis = _trace_replay_axis(payload.get("axis"))
    target = _required_target(payload.get("target"))
    source_request = _mapping(raw_request.payload if raw_request is not None else None)
    source_dimensions = _execution_dimensions(source_run, source_request)
    fixed_dimensions = _fixed_dimensions(source_run, source_request)
    target_dimensions = _target_dimensions(
        source_dimensions,
        axis=axis,
        target=target,
        target_extension=target_extension,
    )
    if source_dimensions == target_dimensions:
        raise ValueError("trace replay must change exactly one execution axis")
    changed = tuple(
        key
        for key in TraceReplayAxis
        if source_dimensions[key.value] != target_dimensions[key.value]
    )
    if changed != (axis,):
        raise ValueError("trace replay must change exactly the selected axis")
    task_sha256 = _task_sha256(source_run, source_request)
    source_evidence_sha256 = trace_evidence_sha256(
        source_run,
        messages=source_messages,
        events=source_events,
    )
    source_unchanged = _unchanged_dimensions(
        source_dimensions,
        axis=axis,
        task_sha256=task_sha256,
        fixed_dimensions=fixed_dimensions,
    )
    target_unchanged = _unchanged_dimensions(
        target_dimensions,
        axis=axis,
        task_sha256=task_sha256,
        fixed_dimensions=fixed_dimensions,
    )
    if source_unchanged != target_unchanged:
        raise ValueError("unchanged trace replay dimensions do not match")
    unchanged_sha256 = _json_sha256(source_unchanged)
    semantic = {
        "schema_version": TRACE_REPLAY_SCHEMA_VERSION,
        "source_run_id": source_run.id,
        "source_session_id": source_run.session_id,
        "task_sha256": task_sha256,
        "source_evidence_sha256": source_evidence_sha256,
        "axis": axis.value,
        "source_dimensions": source_dimensions,
        "target_dimensions": target_dimensions,
        "fixed_dimensions": fixed_dimensions,
        "unchanged_snapshot_sha256": unchanged_sha256,
    }
    manifest = TraceReplayManifest(
        source_run_id=source_run.id,
        source_session_id=source_run.session_id,
        task_sha256=task_sha256,
        source_evidence_sha256=source_evidence_sha256,
        axis=axis,
        source_dimensions=source_dimensions,
        target_dimensions=target_dimensions,
        fixed_dimensions=fixed_dimensions,
        unchanged_snapshot_sha256=unchanged_sha256,
        created_at=created_at,
        manifest_sha256=_json_sha256(semantic),
    )
    replay_payload = build_replay_request(source_run, raw_request=raw_request)
    replay_payload["workspace_policy"] = (
        "worktree" if source_run.workspace is not None else "current"
    )
    replay_payload["model"] = target_dimensions["model"]
    replay_payload["harness_id"] = target_dimensions["harness"]
    extra = dict(_mapping(replay_payload.get("extra")))
    extra["trace_replay"] = {
        "schema_version": TRACE_REPLAY_SCHEMA_VERSION,
        "manifest_sha256": manifest.manifest_sha256,
        "source_run_id": source_run.id,
        "axis": axis.value,
        "provider": target_dimensions["provider"],
        "extensions": target_dimensions["extensions"],
        "content_free": True,
    }
    if axis is TraceReplayAxis.PROVIDER:
        extra["provider_ref"] = dict(_mapping(target_dimensions["provider"]))
    if axis is TraceReplayAxis.EXTENSIONS:
        if target_extension is None:
            extra.pop("managed_mcp_snapshot", None)
            extra.pop("tool_ids", None)
        else:
            extra["managed_mcp_snapshot"] = dict(target_extension)
            extra["tool_ids"] = list(target_extension.get("server_ids") or ())
    replay_payload["extra"] = extra
    return manifest, replay_payload
