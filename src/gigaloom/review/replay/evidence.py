"""Review evidence primitives."""

from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Mapping, Sequence
from gigaloom.review.ports import (
    HarnessMessage,
    HarnessRawRecord,
    HarnessRun,
    HarnessStoredEvent,
)
from gigaloom.review.ports import HarnessSessionStore
from .codec import _mapping


def _comparison(
    source_run: HarnessRun,
    destination_run: HarnessRun,
    *,
    source_messages: Sequence[HarnessMessage],
    destination_messages: Sequence[HarnessMessage],
    source_events: Sequence[HarnessStoredEvent],
    destination_events: Sequence[HarnessStoredEvent],
    destination_terminal: bool,
) -> dict[str, Any]:
    source_semantic = _semantic_evidence(source_run.id, source_messages)
    target_semantic = (
        _semantic_evidence(destination_run.id, destination_messages)
        if destination_terminal
        else None
    )
    source_tools = _tool_evidence(source_events)
    target_tools = _tool_evidence(destination_events) if destination_terminal else None
    source_diff = _diff_evidence(source_run)
    target_diff = _diff_evidence(destination_run) if destination_terminal else None
    source_latency = _latency_evidence(source_run)
    target_latency = (
        _latency_evidence(destination_run) if destination_terminal else None
    )
    source_cost = _cost_evidence(source_run)
    target_cost = _cost_evidence(destination_run) if destination_terminal else None
    return {
        "semantic": _paired_evidence(source_semantic, target_semantic),
        "tools": _paired_evidence(source_tools, target_tools),
        "diff": _paired_evidence(source_diff, target_diff),
        "latency": _paired_numeric(source_latency, target_latency, "milliseconds"),
        "cost": _paired_cost(source_cost, target_cost),
    }


def _semantic_evidence(
    run_id: str,
    messages: Sequence[HarnessMessage],
) -> dict[str, Any]:
    texts = [
        message.content
        for message in messages
        if message.run_id == run_id and message.role == "assistant"
    ]
    encoded = "\n".join(texts).encode("utf-8")
    return {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "message_count": len(texts),
        "byte_count": len(encoded),
    }


def _tool_evidence(events: Sequence[HarnessStoredEvent]) -> dict[str, Any]:
    types: dict[str, int] = {}
    for event in events:
        normalized = event.type.lower()
        payload = _mapping(event.payload)
        if "tool" not in normalized and not any(
            key in payload for key in ("tool", "tool_id", "tool_name", "server_id")
        ):
            continue
        types[event.type] = types.get(event.type, 0) + 1
    return {"event_count": sum(types.values()), "types": dict(sorted(types.items()))}


def _diff_evidence(run: HarnessRun) -> dict[str, Any]:
    workspace = _mapping(run.metadata.get("workspace_execution"))
    patch = workspace.get("patch")
    if not isinstance(patch, str):
        patch = run.metadata.get("diff")
    if not isinstance(patch, str):
        return {
            "sha256": None,
            "available": False,
            "changed_file_count": 0,
            "truncated": bool(workspace.get("truncated")),
        }
    changed_files = workspace.get("changed_files")
    return {
        "sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        "available": True,
        "changed_file_count": (
            len(changed_files)
            if isinstance(changed_files, Sequence)
            and not isinstance(changed_files, (str, bytes, bytearray))
            else 0
        ),
        "truncated": bool(workspace.get("truncated")),
    }


def _latency_evidence(run: HarnessRun) -> int | None:
    if run.started_at is None or run.finished_at is None:
        return None
    try:
        started = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(run.finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((finished - started).total_seconds() * 1000), 0)


def _cost_evidence(run: HarnessRun) -> dict[str, Any]:
    metadata = _mapping(run.metadata)
    value = metadata.get("cost_microunits", metadata.get("cost"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return {"value": None, "unit": None, "confidence": "unknown"}
    confidence = str(metadata.get("cost_confidence") or "measured")
    if confidence not in {"measured", "estimated", "unknown"}:
        confidence = "unknown"
    return {
        "value": value,
        "unit": "microunits" if "cost_microunits" in metadata else "provider_units",
        "confidence": confidence,
    }


def _paired_evidence(
    source: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "source": dict(source),
        "target": dict(target) if target is not None else None,
        "changed": dict(source) != dict(target) if target is not None else None,
    }


def _paired_numeric(
    source: int | None,
    target: int | None,
    unit: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "target": target,
        "delta": (
            target - source if source is not None and target is not None else None
        ),
        "unit": unit,
    }


def _paired_cost(
    source: Mapping[str, Any],
    target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source_value = source.get("value")
    target_value = target.get("value") if target is not None else None
    return {
        "source": dict(source),
        "target": dict(target) if target is not None else None,
        "delta": (
            target_value - source_value
            if isinstance(source_value, (int, float))
            and not isinstance(source_value, bool)
            and isinstance(target_value, (int, float))
            and not isinstance(target_value, bool)
            else None
        ),
    }


def _run_ref(run: HarnessRun) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "session_id": run.session_id,
        "status": run.status.value,
        "harness_id": run.harness_id,
        "model": run.model,
        "workspace_isolated": bool(
            _mapping(run.metadata.get("workspace_execution")).get("worktree_path")
        ),
    }


def _latest_raw_request(
    store: HarnessSessionStore,
    run: HarnessRun,
) -> HarnessRawRecord | None:
    records = tuple(
        record
        for record in store.list_raw_requests(run.session_id)
        if record.run_id == run.id
    )
    return records[-1] if records else None


def _run_messages(
    store: HarnessSessionStore,
    run: HarnessRun,
) -> tuple[HarnessMessage, ...]:
    return tuple(
        message
        for message in store.list_messages(run.session_id)
        if message.run_id == run.id
    )
