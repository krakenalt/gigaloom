"""Bounded content-free artifact projections shared by run read models."""

from __future__ import annotations

import json
from typing import Any, Mapping

from gigaloom.review.api import (
    LANE_DELTA_METADATA_KEY,
    MAX_LANE_DELTA_PACKET_BYTES,
)
from gigaloom.sessions import HarnessRun


def run_artifact_summary(run: HarnessRun | None) -> dict[str, bool]:
    """Return artifact presence without paths or captured content."""
    metadata = dict(run.metadata) if run is not None else {}
    execution = metadata.get("workspace_execution")
    execution = dict(execution) if isinstance(execution, Mapping) else {}
    return {
        "worktree": bool(execution.get("worktree_path")),
        "diff": bool(execution.get("patch") or metadata.get("diff")),
        "pr": isinstance(metadata.get("pr_artifact"), Mapping),
        "lane_delta": isinstance(metadata.get(LANE_DELTA_METADATA_KEY), Mapping),
    }


def run_artifact_inventory(
    artifacts: Mapping[str, bool],
    workflow: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return artifact presence and lineage without paths or captured content."""
    inventory = [
        {"type": artifact_type, "source": "run"}
        for artifact_type in ("worktree", "diff", "pr", "lane_delta")
        if artifacts.get(artifact_type)
    ]
    if workflow:
        for step in workflow.get("steps", ()):
            if not isinstance(step, Mapping):
                continue
            for artifact_type in step.get("artifact_types", ()):
                item = {
                    "type": str(artifact_type),
                    "source": "workflow_step",
                    "step_id": str(step.get("id") or ""),
                }
                if item not in inventory:
                    inventory.append(item)
    return inventory


def cockpit_run_artifacts(run: HarnessRun) -> list[dict[str, Any]]:
    """Return lazy Cockpit artifact metadata with no captured payloads."""
    metadata = dict(run.metadata)
    execution = metadata.get("workspace_execution")
    execution = dict(execution) if isinstance(execution, Mapping) else {}
    artifacts: list[dict[str, Any]] = []
    patch = str(execution.get("patch") or metadata.get("diff") or "")
    if patch:
        artifacts.append(
            {
                "type": "diff",
                "byte_count": len(patch.encode("utf-8")),
                "projection_url": f"/api/cockpit/runs/{run.id}/diff",
            }
        )
    if execution.get("worktree_path"):
        artifacts.append({"type": "worktree", "byte_count": None})
    if isinstance(metadata.get("pr_artifact"), Mapping):
        artifacts.append(
            {
                "type": "pr_report",
                "byte_count": len(retained_run_report(metadata).encode("utf-8")),
                "projection_url": f"/api/cockpit/runs/{run.id}/report",
            }
        )
    lane_delta = metadata.get(LANE_DELTA_METADATA_KEY)
    if isinstance(lane_delta, Mapping) and _valid_lane_delta_artifact(lane_delta):
        artifacts.append(
            {
                "type": "lane_delta",
                "byte_count": lane_delta.get("size_bytes"),
                "sha256": lane_delta.get("packet_sha256"),
                "content_free": True,
            }
        )
    return artifacts


def retained_run_report(metadata: Mapping[str, Any]) -> str:
    """Return the existing report payload for its explicit lazy endpoint."""
    for key in ("pr_artifact", "report", "test_report", "summary"):
        value = metadata.get(key)
        if value:
            return (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            )
    return ""


def _valid_lane_delta_artifact(value: Mapping[str, Any]) -> bool:
    digest = value.get("packet_sha256")
    size_bytes = value.get("size_bytes")
    return (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and not isinstance(size_bytes, bool)
        and isinstance(size_bytes, int)
        and 0 < size_bytes <= MAX_LANE_DELTA_PACKET_BYTES
        and value.get("content_free") is True
        and value.get("hidden_state_portability_claimed") is False
    )


__all__ = [
    "cockpit_run_artifacts",
    "retained_run_report",
    "run_artifact_inventory",
    "run_artifact_summary",
]
