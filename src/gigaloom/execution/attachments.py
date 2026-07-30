"""Typed attachment preparation result for one run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gigaloom.attachments import (
    AttachmentRenderPlan,
    HarnessAttachment,
    attachment_to_dict,
)


@dataclass(frozen=True)
class PreparedAttachments:
    """Loaded attachments and their provider-facing render evidence."""

    attachments: tuple[HarnessAttachment, ...]
    metadata: tuple[dict[str, Any], ...]
    render_plan: AttachmentRenderPlan | None = None
    render_plan_payload: Mapping[str, Any] | None = None


def run_attachment_metadata(attachment: HarnessAttachment) -> dict[str, Any]:
    """Project one attachment without its private storage path."""
    payload = attachment_to_dict(attachment)
    payload.pop("storage_path", None)
    return payload


def message_attachment_metadata(
    attachments: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """Project attachment identities and redacted metadata onto a message."""
    if not attachments:
        return {}
    return {
        "attachment_ids": [str(attachment["id"]) for attachment in attachments],
        "attachments": [dict(attachment) for attachment in attachments],
    }
