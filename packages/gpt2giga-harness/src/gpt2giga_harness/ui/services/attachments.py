"""Attachment request and response helpers for the FastAPI application."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Mapping

from gpt2giga_harness.attachments import (
    AttachmentLimits,
    FilesystemAttachmentStore,
    HarnessAttachment,
    attachment_to_dict,
    limits_from_project_settings,
)
from gpt2giga_harness.project import load_project_config, resolve_project
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.sessions.models import HarnessSession
from gpt2giga_harness.types import HarnessSpec
from gpt2giga_harness.workspace import resolve_workspace


def route_recommendation_attachments(
    payload: Mapping[str, Any],
    *,
    attachment_store: FilesystemAttachmentStore,
) -> tuple[Mapping[str, Any], ...]:
    """Resolve inline and stored attachments for route recommendation."""
    attachments: list[Mapping[str, Any]] = []
    raw_attachments = payload.get("attachments")
    if raw_attachments is not None:
        if not isinstance(raw_attachments, list):
            raise ValueError("attachments must be a list")
        attachments.extend(
            dict(item) for item in raw_attachments if isinstance(item, Mapping)
        )
    raw_ids = payload.get("attachment_ids")
    if raw_ids is not None:
        if not isinstance(raw_ids, list):
            raise ValueError("attachment_ids must be a list")
        for attachment_id in text_tuple(raw_ids):
            attachment = attachment_store.get_attachment(attachment_id)
            attachment_payload = attachment_to_dict(attachment)
            attachment_payload.pop("storage_path", None)
            attachments.append(attachment_payload)
    return tuple(attachments)


def decode_attachment_payload(value: Any) -> bytes:
    """Decode one strict base64 attachment payload."""
    text = _required_text(value, "data_base64 is required")
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("data_base64 is invalid") from exc


def metadata_mapping(value: Any) -> dict[str, Any]:
    """Return a detached metadata mapping or an empty mapping."""
    if isinstance(value, dict):
        return dict(value)
    return {}


def session_project_id(session: HarnessSession) -> str | None:
    """Return the project identity bound to a session."""
    return _optional_text(session.metadata.get("project_id"))


def session_project_root(session: HarnessSession) -> str | None:
    """Return the project or workspace root bound to a session."""
    return _optional_text(session.metadata.get("project_root")) or _optional_text(
        session.workspace
    )


def attachment_limits(
    session: HarnessSession,
    *,
    workspace_root: str | None = None,
) -> AttachmentLimits:
    """Resolve attachment limits for the session project."""
    project_root = workspace_root or session_project_root(session)
    if project_root is None:
        return AttachmentLimits()
    loaded = load_project_config(project_root)
    return limits_from_project_settings(loaded.attachments)


def attachment_workspace(
    session: HarnessSession,
    payload: dict[str, Any],
) -> str:
    """Resolve the bounded workspace for an attachment request."""
    workspace = _optional_text(payload.get("workspace")) or session_project_root(
        session
    )
    if workspace is None:
        raise ValueError("workspace is required")
    return resolve_workspace(workspace)


def workspace_api_root(workspace: str | None, data_dir: str) -> str:
    """Resolve an explicit workspace or the current project root."""
    resolved = resolve_workspace(_optional_text(workspace))
    if resolved is not None:
        return resolved
    return resolve_project(None, data_dir=data_dir).root


def workspace_limits(workspace_root: str) -> AttachmentLimits:
    """Resolve attachment limits for a workspace root."""
    return limits_from_project_settings(load_project_config(workspace_root).attachments)


def attachment_response(
    registry: HarnessRegistry,
    attachment: HarnessAttachment,
) -> dict[str, Any]:
    """Build the public, storage-path-free attachment projection."""
    payload = attachment_to_dict(attachment)
    payload.pop("storage_path", None)
    payload["url"] = f"/api/attachments/{attachment.id}"
    payload["supported_by"] = attachment_supported_by(registry, attachment)
    payload["transport_by"] = attachment_transport_by(registry, attachment)
    payload["warnings"] = attachment_warnings(registry, attachment)
    return payload


def attachment_supported_by(
    registry: HarnessRegistry,
    attachment: HarnessAttachment,
) -> dict[str, bool]:
    """Project attachment compatibility by harness."""
    support: dict[str, bool] = {}
    for harness in registry.list():
        spec = harness.spec()
        support[spec.id] = bool(
            spec.supports_attachments
            and attachment.kind in spec.accepted_attachment_kinds
        )
    return support


def attachment_warnings(
    registry: HarnessRegistry,
    attachment: HarnessAttachment,
) -> list[str]:
    """Describe lossy or unsupported attachment transports."""
    warnings: list[str] = []
    for harness in registry.list():
        spec = harness.spec()
        if not spec.supports_attachments:
            warnings.append(f"{spec.id} does not support attachments.")
        elif attachment.kind not in spec.accepted_attachment_kinds:
            warnings.append(f"{spec.id} does not accept {attachment.kind} attachments.")
        else:
            transport = attachment_transport_for(spec, attachment)
            if (
                transport
                and not transport["rich"]
                and effective_attachment_kind(attachment) in {"image", "document"}
            ):
                warnings.append(
                    f"{spec.id} uses path or metadata reference only for "
                    f"{effective_attachment_kind(attachment)} attachments."
                )
    return warnings


def attachment_transport_by(
    registry: HarnessRegistry,
    attachment: HarnessAttachment,
) -> dict[str, dict[str, Any]]:
    """Project selected attachment transports by harness."""
    return {
        harness.spec().id: transport
        for harness in registry.list()
        if (transport := attachment_transport_for(harness.spec(), attachment))
    }


def attachment_transport_for(
    spec: HarnessSpec,
    attachment: HarnessAttachment,
) -> dict[str, Any]:
    """Project one harness transport for an attachment."""
    capabilities = getattr(spec, "attachment_capabilities", {})
    if not isinstance(capabilities, Mapping):
        return {}
    support = capabilities.get(effective_attachment_kind(attachment))
    if support is None:
        support = capabilities.get(attachment.kind)
    if support is None:
        return {}
    if isinstance(support, Mapping):
        headless = support.get("headless", ())
        native = support.get("native", ())
        rich = bool(support.get("rich", False))
        required = support.get("required_cli_capabilities", ())
        detail = str(support.get("detail") or "")
    else:
        headless = getattr(support, "headless", ())
        native = getattr(support, "native", ())
        rich = bool(getattr(support, "rich", False))
        required = getattr(support, "required_cli_capabilities", ())
        detail = str(getattr(support, "detail", ""))
    return {
        "headless": [str(item) for item in headless],
        "native": [str(item) for item in native],
        "rich": rich,
        "required_cli_capabilities": [str(item) for item in required],
        "detail": detail,
    }


def effective_attachment_kind(attachment: HarnessAttachment) -> str:
    """Return the detected kind for a workspace-file attachment."""
    if attachment.kind == "workspace_file":
        detected = attachment.metadata.get("detected_kind")
        if isinstance(detected, str) and detected:
            return detected
    return attachment.kind


def content_disposition(filename: str, *, inline: bool) -> str:
    """Build a safe attachment content-disposition value."""
    safe = "".join(
        char for char in filename if char.isalnum() or char in {" ", ".", "_", "-"}
    ).strip()
    if not safe:
        safe = "attachment"
    disposition = "inline" if inline else "attachment"
    return f'{disposition}; filename="{safe}"'


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any, message: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(message)
    return text


def text_tuple(value: Any) -> tuple[str, ...]:
    """Normalize a list payload to non-empty text values."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    items: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            items.append(text)
    return tuple(items)
