"""Typed resources projections for TUI clients."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


from gpt2giga_harness.attachments import (
    AttachmentLimits,
    limits_from_project_settings,
)
from gpt2giga_harness.attachments.limits import normalize_workspace_file
from gpt2giga_harness.project import (
    load_project_config,
)
from gpt2giga_harness.sessions.models import (
    HarnessSession,
)

from gpt2giga_harness.tui.contracts import (
    MAX_FILE_CANDIDATES,
    MAX_FILE_PREVIEW_CHARS,
    WorkbenchClientError,
    FileCandidate,
    AttachmentSummary,
    HandoffPreview,
)

from gpt2giga_harness.tui.projections.values import (
    _bounded_non_negative_int,
    _bounded_content_text,
    _mapping,
    _required_text,
    _optional_text,
    _display_text,
    _required_identity,
)


def _workspace_limits(workspace: str) -> AttachmentLimits:
    return limits_from_project_settings(load_project_config(workspace).attachments)


def _session_workspace(session: HarnessSession) -> str:
    workspace = _optional_text(session.workspace)
    if workspace is None:
        raise WorkbenchClientError("session has no project workspace")
    return workspace


def _attachment_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > MAX_FILE_CANDIDATES:
        raise WorkbenchClientError("too many attachments selected")
    return tuple(
        _required_identity(value, "attachment id") for value in dict.fromkeys(values)
    )


def _file_candidate(
    data: Mapping[str, Any],
    *,
    workspace: str,
    limits: AttachmentLimits,
) -> FileCandidate:
    path = _required_text(data.get("path"), "file path")
    kind = _display_text(data.get("kind") or "unknown")
    preview = "Preview is unavailable for this file type."
    preview_status = "unsupported"
    if kind == "text":
        resolved, _relative = normalize_workspace_file(workspace, path, limits)
        raw = resolved.read_bytes()[: MAX_FILE_PREVIEW_CHARS + 1]
        preview = _bounded_content_text(
            raw[:MAX_FILE_PREVIEW_CHARS].decode("utf-8", errors="replace"),
            MAX_FILE_PREVIEW_CHARS,
        )
        preview_status = "truncated" if len(raw) > MAX_FILE_PREVIEW_CHARS else "ready"
    return FileCandidate(
        path=path,
        name=_display_text(data.get("name") or Path(path).name),
        mime_type=_display_text(data.get("mime_type") or "application/octet-stream"),
        kind=kind,
        size_bytes=_bounded_non_negative_int(data.get("size_bytes")),
        preview=preview,
        preview_status=preview_status,
    )


def _file_candidate_from_mapping(
    data: Mapping[str, Any], preview_response: Mapping[str, Any]
) -> FileCandidate:
    preview = _mapping(preview_response.get("preview"))
    return FileCandidate(
        path=_required_text(data.get("path"), "file path"),
        name=_display_text(data.get("name") or "file"),
        mime_type=_display_text(data.get("mime_type") or "application/octet-stream"),
        kind=_display_text(data.get("kind") or "unknown"),
        size_bytes=_bounded_non_negative_int(data.get("size_bytes")),
        preview=_bounded_content_text(
            preview.get("text") or "Preview is unavailable for this file type.",
            MAX_FILE_PREVIEW_CHARS,
        ),
        preview_status=_display_text(preview.get("status") or "unsupported"),
    )


def _attachment_summary(data: Mapping[str, Any]) -> AttachmentSummary:
    return AttachmentSummary(
        id=_required_identity(data.get("id"), "attachment id"),
        path=_required_text(
            data.get("workspace_path") or data.get("filename"), "attachment path"
        ),
        mime_type=_display_text(data.get("mime_type") or "application/octet-stream"),
        kind=_display_text(data.get("kind") or "attachment"),
        size_bytes=_bounded_non_negative_int(data.get("size_bytes")),
    )


def _provider_handoff_from_mapping(
    data: Mapping[str, Any], *, harness_id: str
) -> HandoffPreview:
    command = tuple(
        _display_text(item)
        for item in (
            data.get("command") if isinstance(data.get("command"), list) else ()
        )
    )[:20]
    limits = tuple(
        _display_text(item)
        for item in (
            data.get("observability_limits")
            if isinstance(data.get("observability_limits"), list)
            else ()
        )
    )[:20]
    return HandoffPreview(
        kind="provider",
        status=_display_text(data.get("status") or "blocked"),
        target=_display_text(data.get("surface") or harness_id),
        continuity=(
            "Provider owns the external UI; Harness retains only the current durable session."
        ),
        observability=limits or ("structured provider observability is unavailable",),
        instruction=_display_text(
            data.get("instruction") or data.get("blocker") or "Handoff unavailable"
        ),
        command=command,
    )


def _blocked_provider_handoff(harness_id: str) -> HandoffPreview:
    return HandoffPreview(
        kind="provider",
        status="blocked",
        target=_display_text(harness_id),
        continuity="Harness session remains authoritative and unchanged.",
        observability=("provider UI handoff is not advertised by this Harness",),
        instruction="Continue in the TUI or use an explicit provider-owned terminal flow.",
    )
