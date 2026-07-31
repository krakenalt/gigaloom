"""Review manifest primitives."""

from __future__ import annotations

from typing import Any, Mapping
from .codec import (
    _bounded_text,
    _json_sha256,
    _mapping,
    _reject_unknown_fields,
    _required_hash,
    _required_identity,
    _required_text,
    _trace_replay_axis,
)
from .dimensions import _extension_dimensions
from .models import TRACE_REPLAY_SCHEMA_VERSION, TraceReplayAxis, TraceReplayManifest


def manifest_from_dict(value: Mapping[str, Any]) -> TraceReplayManifest:
    """Strictly load one retained Trace-to-Replay manifest."""
    data = _mapping(value)
    allowed = {
        "schema_version",
        "source_run_id",
        "source_session_id",
        "task_sha256",
        "source_evidence_sha256",
        "axis",
        "source_dimensions",
        "target_dimensions",
        "fixed_dimensions",
        "unchanged_snapshot_sha256",
        "created_at",
        "manifest_sha256",
        "content_free",
    }
    _reject_unknown_fields(data, allowed)
    if data.get("schema_version") != TRACE_REPLAY_SCHEMA_VERSION:
        raise ValueError("unsupported trace replay schema_version")
    manifest = TraceReplayManifest(
        source_run_id=_required_identity(data.get("source_run_id"), "source run id"),
        source_session_id=_required_identity(
            data.get("source_session_id"), "source session id"
        ),
        task_sha256=_required_hash(data.get("task_sha256"), "task_sha256"),
        source_evidence_sha256=_required_hash(
            data.get("source_evidence_sha256"), "source_evidence_sha256"
        ),
        axis=_trace_replay_axis(data.get("axis")),
        source_dimensions=_strict_dimensions(data.get("source_dimensions")),
        target_dimensions=_strict_dimensions(data.get("target_dimensions")),
        fixed_dimensions=_strict_fixed_dimensions(data.get("fixed_dimensions")),
        unchanged_snapshot_sha256=_required_hash(
            data.get("unchanged_snapshot_sha256"),
            "unchanged_snapshot_sha256",
        ),
        created_at=_required_text(data.get("created_at"), "created_at"),
        manifest_sha256=_required_hash(data.get("manifest_sha256"), "manifest_sha256"),
    )
    semantic = manifest.to_dict()
    semantic.pop("created_at")
    semantic.pop("manifest_sha256")
    semantic.pop("content_free")
    if _json_sha256(semantic) != manifest.manifest_sha256:
        raise ValueError("trace replay manifest hash mismatch")
    changed = tuple(
        axis
        for axis in TraceReplayAxis
        if manifest.source_dimensions[axis.value]
        != manifest.target_dimensions[axis.value]
    )
    if changed != (manifest.axis,):
        raise ValueError("trace replay manifest does not contain exactly one axis")
    return manifest


def _strict_dimensions(value: Any) -> dict[str, Any]:
    dimensions = _mapping(value)
    if set(dimensions) != {axis.value for axis in TraceReplayAxis}:
        raise ValueError("trace replay dimensions are incomplete")
    return {
        "model": (
            _bounded_text(dimensions.get("model"), "model")
            if dimensions.get("model") is not None
            else None
        ),
        "provider": (
            {
                "id": _required_identity(
                    _mapping(dimensions.get("provider")).get("id"), "provider id"
                ),
                "revision": _required_identity(
                    _mapping(dimensions.get("provider")).get("revision"),
                    "provider revision",
                ),
            }
            if dimensions.get("provider") is not None
            else None
        ),
        "harness": _required_identity(dimensions.get("harness"), "harness"),
        "extensions": _extension_dimensions(
            _mapping(dimensions.get("extensions")) or None
        ),
    }


def _strict_fixed_dimensions(value: Any) -> dict[str, Any]:
    dimensions = _mapping(value)
    expected = {
        "api_mode",
        "capability",
        "mode",
        "invocation_mode",
        "execution_transport",
        "stream",
        "workspace_sha256",
        "permission_profile",
    }
    if set(dimensions) != expected:
        raise ValueError("trace replay fixed dimensions are incomplete")
    if not isinstance(dimensions.get("stream"), bool):
        raise ValueError("trace replay stream dimension must be a boolean")
    workspace_sha256 = dimensions.get("workspace_sha256")
    permission_profile = dimensions.get("permission_profile")
    execution_transport = dimensions.get("execution_transport")
    return {
        "api_mode": _required_identity(dimensions.get("api_mode"), "api mode"),
        "capability": _required_identity(dimensions.get("capability"), "capability"),
        "mode": _required_identity(dimensions.get("mode"), "mode"),
        "invocation_mode": _required_identity(
            dimensions.get("invocation_mode"), "invocation mode"
        ),
        "execution_transport": (
            _required_identity(execution_transport, "execution transport")
            if execution_transport is not None
            else None
        ),
        "stream": dimensions["stream"],
        "workspace_sha256": (
            _required_hash(workspace_sha256, "workspace_sha256")
            if workspace_sha256 is not None
            else None
        ),
        "permission_profile": (
            _required_identity(permission_profile, "permission profile")
            if permission_profile is not None
            else None
        ),
    }
