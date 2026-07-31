"""Review projections primitives."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from gigaloom.review.ports import HarnessRun, HarnessStoredEvent
from gigaloom.projects.api import run_diff_response
from .codec import (
    _encoded_size,
    _first_hash,
    _mapping_or_empty,
    _optional_identity,
    _semantic_hash,
    _string_sequence,
)
from .models import (
    MAX_CAPSULE_ARTIFACTS,
    MAX_CAPSULE_QUESTIONS,
    _QUESTION_REQUEST_MARKERS,
    _QUESTION_RESOLUTION_MARKERS,
)


def _diff_projection(run: HarnessRun) -> dict[str, Any]:
    diff = run_diff_response(run.metadata)
    execution = _mapping_or_empty(diff.get("workspace_execution"))
    patch = str(diff.get("patch") or "")
    return {
        "sha256": _semantic_hash("run-diff", patch),
        "byte_count": len(patch.encode("utf-8")),
        "captured": bool(patch and patch != "No diff captured."),
        "truncated": bool(execution.get("truncated")),
        "changed_file_count": len(_string_sequence(diff.get("changed_files"))),
        "untracked_file_count": len(_string_sequence(diff.get("untracked_files"))),
        "path_identities": sorted(
            _semantic_hash("run-diff-path", path)
            for path in (
                *_string_sequence(diff.get("changed_files")),
                *_string_sequence(diff.get("untracked_files")),
            )
        )[:MAX_CAPSULE_ARTIFACTS],
    }


def _artifact_projection(
    run: HarnessRun,
    events: Sequence[HarnessStoredEvent],
    environment: Mapping[str, Any],
) -> list[dict[str, Any]]:
    diff = _diff_projection(run)
    artifacts: list[dict[str, Any]] = []
    if diff["captured"]:
        artifacts.append(
            {
                "type": "diff",
                "identity_sha256": diff["sha256"],
                "byte_count": diff["byte_count"],
            }
        )
    execution = _mapping_or_empty(run.metadata.get("workspace_execution"))
    if execution.get("worktree_path"):
        artifacts.append(
            {
                "type": "worktree",
                "identity_sha256": environment["snapshot_sha256"],
                "byte_count": None,
            }
        )
    for key, artifact_type in (
        ("pr_artifact", "pr_report"),
        ("report", "report"),
        ("test_report", "test_report"),
    ):
        value = run.metadata.get(key)
        if value is not None:
            artifacts.append(
                {
                    "type": artifact_type,
                    "identity_sha256": _semantic_hash(artifact_type, value),
                    "byte_count": _encoded_size(value),
                }
            )
    for event in events:
        normalized = event.type.casefold().replace(".", "_").replace("-", "_")
        if normalized not in {"generated_file", "file_changed"}:
            continue
        artifacts.append(
            {
                "type": normalized,
                "identity_sha256": _semantic_hash(
                    f"event-artifact:{event.id}", event.payload
                ),
                "byte_count": None,
            }
        )
        if len(artifacts) >= MAX_CAPSULE_ARTIFACTS:
            break
    return sorted(
        artifacts[:MAX_CAPSULE_ARTIFACTS],
        key=lambda item: (str(item["type"]), str(item["identity_sha256"])),
    )


def _tool_extension_projection(run: HarnessRun) -> dict[str, Any]:
    metadata = _mapping_or_empty(run.metadata)
    provenance = _mapping_or_empty(metadata.get("provenance"))
    execution = _mapping_or_empty(provenance.get("execution"))
    managed = _mapping_or_empty(metadata.get("managed_mcp_snapshot"))
    if not managed:
        managed = _mapping_or_empty(execution.get("managed_mcp_snapshot"))
    snapshot_hash = _first_hash(
        managed.get("snapshot_hash"),
        metadata.get("extension_snapshot_hash"),
        execution.get("extension_snapshot_hash"),
    )
    server_ids = _string_sequence(managed.get("server_ids"))
    projection = {
        "managed_mcp_snapshot_id": _optional_identity(managed.get("snapshot_id")),
        "snapshot_sha256": snapshot_hash,
        "server_count": len(server_ids),
        "server_identities": sorted(
            _semantic_hash("managed-mcp-server", item) for item in server_ids
        ),
        "descriptor_contents_included": False,
        "secret_values_included": False,
    }
    return {
        **projection,
        "projection_sha256": _semantic_hash("tool-extension-snapshot", projection),
    }


def _provider_projection(run: HarnessRun) -> dict[str, Any]:
    metadata = _mapping_or_empty(run.metadata)
    provenance = _mapping_or_empty(metadata.get("provenance"))
    request = _mapping_or_empty(provenance.get("request"))
    extra = _mapping_or_empty(request.get("extra"))
    provider_ref = _mapping_or_empty(
        metadata.get("provider_ref") or extra.get("provider_ref")
    )
    return {
        "id": _optional_identity(
            provider_ref.get("id")
            or provider_ref.get("provider_id")
            or metadata.get("provider_id")
        ),
        "revision": _optional_identity(provider_ref.get("revision")),
        "model_sha256": (
            _semantic_hash("model", run.model) if run.model is not None else None
        ),
    }


def _unresolved_questions(
    events: Sequence[HarnessStoredEvent],
) -> list[dict[str, Any]]:
    pending: dict[str, HarnessStoredEvent] = {}
    for event in events:
        normalized = event.type.casefold().replace(".", "_").replace("-", "_")
        request_id = _question_identity(event)
        if any(marker in normalized for marker in _QUESTION_RESOLUTION_MARKERS):
            pending.pop(request_id, None)
        elif any(marker in normalized for marker in _QUESTION_REQUEST_MARKERS):
            pending[request_id] = event
    return [
        {
            "id": _semantic_hash("question-id", request_id),
            "type": event.type,
            "created_at": event.created_at,
            "content_included": False,
        }
        for request_id, event in sorted(pending.items())[:MAX_CAPSULE_QUESTIONS]
    ]


def _question_identity(event: HarnessStoredEvent) -> str:
    for key in ("request_id", "input_id", "elicitation_id", "id"):
        value = _optional_identity(event.payload.get(key))
        if value is not None:
            return value
    return event.id
