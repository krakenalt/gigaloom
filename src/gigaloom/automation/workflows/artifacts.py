"""Artifacts for the workflows subcontext."""

from __future__ import annotations

from typing import Any, Mapping
from .constants import (
    HANDOFF_ARTIFACT_TYPES as HANDOFF_ARTIFACT_TYPES,
    MAX_HANDOFF_PATCH_PREVIEW_CHARS as MAX_HANDOFF_PATCH_PREVIEW_CHARS,
)
from .definitions import _mapping as _mapping


def _typed_run_artifacts(
    child_run: Any, outputs: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    """Project one child run into the strict typed handoff artifact vocabulary."""
    metadata = _mapping(child_run.metadata)
    execution = _mapping(metadata.get("workspace_execution"))
    agent = _mapping(outputs.get("agent"))
    summary = str(outputs.get("summary") or "")
    artifacts: list[dict[str, Any]] = []
    mode = str(agent.get("mode") or "")
    expected = str(
        _mapping(metadata.get("agent_profile_snapshot")).get("expected_artifact") or ""
    )
    if mode == "plan" or expected == "plan":
        artifacts.append({"type": "plan", "run_id": child_run.id, "preview": summary})
    if mode == "read" or expected == "review_findings":
        artifact_type = (
            "test_report" if expected == "test_report" else "review_findings"
        )
        artifacts.append(
            {"type": artifact_type, "run_id": child_run.id, "preview": summary}
        )
    changed_files = tuple(
        dict.fromkeys(
            str(item)
            for item in (
                *(execution.get("changed_files") or ()),
                *(execution.get("untracked_files") or ()),
            )
        )
    )
    patch = str(execution.get("patch") or "")
    if changed_files:
        artifacts.append(
            {
                "type": "selected_files",
                "run_id": child_run.id,
                "paths": list(changed_files),
            }
        )
    if patch:
        preview = patch[:MAX_HANDOFF_PATCH_PREVIEW_CHARS]
        for artifact_type in ("patch", "diff"):
            artifacts.append(
                {
                    "type": artifact_type,
                    "run_id": child_run.id,
                    "changed_files": list(changed_files),
                    "preview": preview,
                    "truncated": len(patch) > len(preview),
                }
            )
        if isinstance(metadata.get("pr_artifact"), Mapping):
            artifacts.append(
                {"type": "pr_draft", "run_id": child_run.id, "preview": summary}
            )
    return tuple(
        artifact for artifact in artifacts if artifact["type"] in HANDOFF_ARTIFACT_TYPES
    )
