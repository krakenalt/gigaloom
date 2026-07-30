"""Evidence for the arena subcontext."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from gigaloom.automation.ports import HarnessRun
from gigaloom.automation.ports import HarnessSessionStore, SessionNotFoundError
from .codec import _mapping as _mapping
from .constants import ARENA_REVIEW_SCHEMA_VERSION as ARENA_REVIEW_SCHEMA_VERSION
from .models import (
    HarnessArenaChildRun as HarnessArenaChildRun,
    HarnessArenaRequest as HarnessArenaRequest,
)


def _arena_task_sha256(request: HarnessArenaRequest) -> str:
    return _mapping_sha256(
        {
            "schema_version": ARENA_REVIEW_SCHEMA_VERSION,
            "prompt": request.prompt,
            "model": request.model,
            "api_mode": request.api_mode.value,
            "mode": request.mode,
            "workspace": request.workspace,
            "attachment_ids": list(request.attachment_ids),
            "workspace_policy": request.workspace_policy,
            "execution_transport": (
                request.execution_transport.value
                if request.execution_transport is not None
                else None
            ),
        }
    )


def _arena_candidate_evidence(
    child: HarnessArenaChildRun,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "child_index": child.index,
        "harness_id": child.harness_id,
        "session_id": child.session_id,
        "run_id": child.run_id,
        "status": child.status,
        "configuration_sha256": None,
        "artifact_sha256": None,
        "metrics": {},
        "cost": {"confidence": "unknown", "value": None, "unit": None},
    }
    if child.run_id is None:
        return evidence
    try:
        run = store.get_run(child.run_id)
    except (KeyError, SessionNotFoundError):
        return evidence
    metadata = dict(run.metadata)
    usage = _mapping(metadata.get("usage"))
    evidence["configuration_sha256"] = _mapping_sha256(
        {
            "harness_id": run.harness_id,
            "model": run.model,
            "api_mode": run.api_mode.value,
            "mode": run.mode,
            "invocation_mode": run.invocation_mode.value,
            "workspace_policy": _mapping(metadata.get("workspace_execution")).get(
                "requested_policy"
            ),
        }
    )
    workspace_execution = _mapping(metadata.get("workspace_execution"))
    patch = workspace_execution.get("patch")
    if isinstance(patch, str) and patch and patch != "No diff captured.":
        evidence["artifact_sha256"] = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    metrics: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[key] = value
    changed_files = workspace_execution.get("changed_files")
    if isinstance(changed_files, list):
        metrics["changed_files"] = len(changed_files)
    duration_ms = _run_duration_ms(run)
    if duration_ms is not None:
        metrics["duration_ms"] = duration_ms
    evidence["metrics"] = metrics
    cost = usage.get("cost_usd", metadata.get("cost_usd"))
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        evidence["cost"] = {
            "confidence": "exact",
            "value": float(cost),
            "unit": "USD",
        }
    return evidence


def _run_duration_ms(run: HarnessRun) -> int | None:
    from datetime import datetime

    if not run.started_at or not run.finished_at:
        return None
    try:
        started = datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
        finished = datetime.fromisoformat(run.finished_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(int((finished - started).total_seconds() * 1000), 0)


def _arena_scores(
    value: Any,
    candidates: list[dict[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError("scores must be a list")
    expected = {int(item["child_index"]) for item in candidates}
    parsed: dict[int, float] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("scores must contain objects")
        index = _bounded_child_index(item.get("child_index"))
        score = item.get("score")
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 0 <= float(score) <= 1
        ):
            raise ValueError("every score must be between 0 and 1")
        if index in parsed:
            raise ValueError("scores contain a duplicate child_index")
        parsed[index] = round(float(score), 6)
    if set(parsed) != expected:
        raise ValueError("scores must cover every arena candidate exactly once")
    return tuple(
        {"child_index": index, "score": parsed[index]} for index in sorted(parsed)
    )


def _bounded_child_index(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("selected_child_index must be an integer")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("selected_child_index must be an integer") from exc
    if index < 0 or index > 31:
        raise ValueError("selected_child_index is out of bounds")
    return index


def _arena_promotion_projection(run_id: str) -> dict[str, Any]:
    if not run_id:
        return {}
    return {
        "selected_run_id": run_id,
        "configuration_preview_url": f"/api/runs/{run_id}/promotions/preview",
        "artifact_review_url": f"/api/runs/{run_id}/diff",
        "run_url": f"/web/runs/{run_id}",
        "automatic_apply": False,
    }


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
