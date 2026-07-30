"""Review dimensions primitives."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence
from gpt2giga_harness.review.ports import HarnessMessage, HarnessRun, HarnessStoredEvent
from .codec import (
    _bounded_text,
    _json_sha256,
    _mapping,
    _required_hash,
    _required_identity,
)
from .evidence import (
    _cost_evidence,
    _diff_evidence,
    _latency_evidence,
    _semantic_evidence,
    _tool_evidence,
)
from .models import TraceReplayAxis


def trace_evidence_sha256(
    run: HarnessRun,
    *,
    messages: Sequence[HarnessMessage],
    events: Sequence[HarnessStoredEvent],
) -> str:
    """Return a bounded content-free identity for retained comparison evidence."""
    return _json_sha256(
        {
            "run_id": run.id,
            "status": run.status.value,
            "semantic": _semantic_evidence(run.id, messages),
            "tools": _tool_evidence(events),
            "diff": _diff_evidence(run),
            "latency": _latency_evidence(run),
            "cost": _cost_evidence(run),
        }
    )


def extension_target_reference(target: str) -> dict[str, str] | None:
    """Parse a content-free managed MCP target reference from UI/API text."""
    normalized = target.strip()
    if normalized == "none":
        return None
    snapshot_id, separator, snapshot_hash = normalized.partition("@")
    if (
        not separator
        or not snapshot_id.startswith("mcp_")
        or not snapshot_id[4:].isalnum()
    ):
        raise ValueError("extension target must be none or mcp_<id>@<sha256>")
    return {
        "snapshot_id": snapshot_id,
        "snapshot_hash": _required_hash(snapshot_hash, "extension snapshot hash"),
    }


def _target_dimensions(
    source: Mapping[str, Any],
    *,
    axis: TraceReplayAxis,
    target: str,
    target_extension: Mapping[str, Any] | None,
) -> dict[str, Any]:
    dimensions = dict(source)
    if axis is TraceReplayAxis.MODEL:
        dimensions["model"] = _bounded_text(target, "model")
    elif axis is TraceReplayAxis.HARNESS:
        dimensions["harness"] = _required_identity(target, "harness")
    elif axis is TraceReplayAxis.PROVIDER:
        provider_id, separator, revision = target.partition("@")
        if not separator:
            raise ValueError("provider target must be <id>@<revision>")
        dimensions["provider"] = {
            "id": _required_identity(provider_id, "provider id"),
            "revision": _required_identity(revision, "provider revision"),
        }
    else:
        dimensions["extensions"] = _extension_dimensions(target_extension)
    return dimensions


def _execution_dimensions(
    run: HarnessRun,
    raw_request: Mapping[str, Any],
) -> dict[str, Any]:
    extra = _mapping(raw_request.get("extra"))
    return {
        "model": run.model,
        "provider": _provider_dimensions(run, extra),
        "harness": run.harness_id,
        "extensions": _extension_dimensions(
            _mapping(extra.get("managed_mcp_snapshot"))
            or _mapping(run.metadata.get("managed_mcp_snapshot"))
            or None
        ),
    }


def _fixed_dimensions(
    run: HarnessRun,
    raw_request: Mapping[str, Any],
) -> dict[str, Any]:
    extra = _mapping(raw_request.get("extra"))
    execution_snapshot = _mapping(run.metadata.get("execution_snapshot"))
    return {
        "api_mode": run.api_mode.value,
        "capability": run.capability.value,
        "mode": run.mode,
        "invocation_mode": run.invocation_mode.value,
        "execution_transport": (
            raw_request.get("execution_transport")
            or run.metadata.get("execution_transport")
        ),
        "stream": bool(raw_request.get("stream")),
        "workspace_sha256": (
            hashlib.sha256(run.workspace.encode("utf-8")).hexdigest()
            if run.workspace is not None
            else None
        ),
        "permission_profile": (
            extra.get("permission_profile")
            or execution_snapshot.get("permission_profile")
        ),
    }


def _extension_source_reference(
    run: HarnessRun,
    raw_request: Mapping[str, Any],
) -> Mapping[str, Any]:
    extra = _mapping(raw_request.get("extra"))
    return _mapping(extra.get("managed_mcp_snapshot")) or _mapping(
        run.metadata.get("managed_mcp_snapshot")
    )


def _provider_dimensions(
    run: HarnessRun,
    extra: Mapping[str, Any],
) -> dict[str, str] | None:
    direct = _mapping(extra.get("provider_ref"))
    if direct:
        return {
            "id": _required_identity(direct.get("id"), "provider id"),
            "revision": _required_identity(direct.get("revision"), "provider revision"),
        }
    for candidate in (
        _mapping(run.metadata.get("execution_snapshot")),
        _mapping(
            _mapping(run.metadata.get("structured_session_link")).get(
                "execution_snapshot"
            )
        ),
    ):
        provider = _mapping(candidate.get("provider"))
        if provider:
            return {
                "id": _required_identity(provider.get("id"), "provider id"),
                "revision": _required_identity(
                    provider.get("revision"), "provider revision"
                ),
            }
    return None


def _extension_dimensions(
    reference: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not reference:
        return None
    snapshot_id = _required_identity(reference.get("snapshot_id"), "snapshot id")
    snapshot_hash = _required_hash(reference.get("snapshot_hash"), "snapshot hash")
    server_ids = reference.get("server_ids")
    return {
        "snapshot_id": snapshot_id,
        "snapshot_hash": snapshot_hash,
        "server_ids": (
            sorted(
                {_required_identity(item, "extension server id") for item in server_ids}
            )
            if isinstance(server_ids, Sequence)
            and not isinstance(server_ids, (str, bytes, bytearray))
            else []
        ),
    }


def _task_sha256(run: HarnessRun, raw_request: Mapping[str, Any]) -> str:
    attachments = raw_request.get("attachments")
    safe_attachments: list[dict[str, Any]] = []
    if isinstance(attachments, Sequence) and not isinstance(
        attachments, (str, bytes, bytearray)
    ):
        for item in attachments[:64]:
            attachment = _mapping(item)
            safe_attachments.append(
                {
                    "id": attachment.get("id"),
                    "sha256": attachment.get("sha256"),
                    "size_bytes": attachment.get("size_bytes"),
                }
            )
    prompt = (
        str(raw_request.get("original_prompt") or "")
        or str(raw_request.get("prompt") or "")
        or run.prompt
    )
    return _json_sha256(
        {
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "attachments": safe_attachments,
        }
    )


def _unchanged_dimensions(
    dimensions: Mapping[str, Any],
    *,
    axis: TraceReplayAxis,
    task_sha256: str,
    fixed_dimensions: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "task_sha256": task_sha256,
        "fixed_dimensions": dict(fixed_dimensions),
        "dimensions": {
            key: value for key, value in dimensions.items() if key != axis.value
        },
    }
