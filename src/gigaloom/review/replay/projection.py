"""Review projection primitives."""

from __future__ import annotations

from typing import Any, Sequence
from gigaloom.review.ports import (
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessStoredEvent,
)
from .codec import _json_sha256, _mapping
from .dimensions import (
    _execution_dimensions,
    _fixed_dimensions,
    _task_sha256,
    _unchanged_dimensions,
    trace_evidence_sha256,
)
from .evidence import _comparison, _run_ref
from .models import (
    TRACE_REPLAY_SCHEMA_VERSION,
    TraceReplayAxis,
    TraceReplayManifest,
    _ACTIVE_STATUSES,
)


def trace_replay_projection(
    manifest: TraceReplayManifest,
    *,
    source_run: HarnessRun,
    destination_run: HarnessRun,
    source_raw_request: HarnessRawRecord | None,
    destination_raw_request: HarnessRawRecord | None,
    source_messages: Sequence[HarnessMessage],
    destination_messages: Sequence[HarnessMessage],
    source_events: Sequence[HarnessStoredEvent],
    destination_events: Sequence[HarnessStoredEvent],
) -> dict[str, Any]:
    """Compare one destination with the immutable source replay manifest."""
    source_current_evidence = trace_evidence_sha256(
        source_run,
        messages=source_messages,
        events=source_events,
    )
    actual_dimensions = None
    equality = {
        "status": "pending",
        "changed_axes": [],
        "unchanged_verified": False,
        "target_verified": False,
    }
    if destination_raw_request is not None:
        destination_request = _mapping(destination_raw_request.payload)
        actual_dimensions = _execution_dimensions(destination_run, destination_request)
        changed_axes = [
            axis.value
            for axis in TraceReplayAxis
            if manifest.source_dimensions[axis.value] != actual_dimensions[axis.value]
        ]
        actual_unchanged = _json_sha256(
            _unchanged_dimensions(
                actual_dimensions,
                axis=manifest.axis,
                task_sha256=_task_sha256(destination_run, destination_request),
                fixed_dimensions=_fixed_dimensions(
                    destination_run, destination_request
                ),
            )
        )
        equality = {
            "status": (
                "verified"
                if actual_dimensions == manifest.target_dimensions
                and actual_unchanged == manifest.unchanged_snapshot_sha256
                else "mismatch"
            ),
            "changed_axes": changed_axes,
            "unchanged_verified": (
                actual_unchanged == manifest.unchanged_snapshot_sha256
            ),
            "target_verified": actual_dimensions == manifest.target_dimensions,
        }
    destination_terminal = destination_run.status.value not in _ACTIVE_STATUSES
    return {
        "schema_version": TRACE_REPLAY_SCHEMA_VERSION,
        "manifest": manifest.to_dict(),
        "source": _run_ref(source_run),
        "destination": _run_ref(destination_run),
        "source_evidence_current": (
            source_current_evidence == manifest.source_evidence_sha256
        ),
        "snapshot_equality": equality,
        "comparison_status": "ready" if destination_terminal else "pending",
        "comparison": _comparison(
            source_run,
            destination_run,
            source_messages=source_messages,
            destination_messages=destination_messages,
            source_events=source_events,
            destination_events=destination_events,
            destination_terminal=destination_terminal,
        ),
        "external_telemetry_required": False,
        "automatic_apply": False,
    }
