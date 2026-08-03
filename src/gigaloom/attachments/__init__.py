"""Attachment models and storage for the Unified Harness cockpit."""

from gigaloom.attachments.encoding import (
    DecodedAttachmentText,
    TextAttachmentDecodeError,
    decode_attachment_text,
)
from gigaloom.attachments.limits import (
    AttachmentLimits,
    AttachmentValidationError,
    limits_from_project_settings,
)
from gigaloom.attachments.models import (
    AttachmentKind,
    AttachmentRenderPlan,
    HarnessAttachment,
    attachment_from_dict,
    attachment_to_dict,
    render_plan_from_dict,
    render_plan_to_dict,
)
from gigaloom.attachments.renderers import (
    render_attachments_for_harness,
    render_for_claude_code,
    render_for_codex_cli,
    render_for_direct_chat,
    render_for_echo,
    render_for_gemini_cli,
)
from gigaloom.attachments.store import (
    AttachmentNotFoundError,
    AttachmentSessionNotFoundError,
    FilesystemAttachmentStore,
)

__all__ = [
    "AttachmentKind",
    "AttachmentLimits",
    "AttachmentNotFoundError",
    "AttachmentRenderPlan",
    "AttachmentSessionNotFoundError",
    "AttachmentValidationError",
    "DecodedAttachmentText",
    "FilesystemAttachmentStore",
    "HarnessAttachment",
    "TextAttachmentDecodeError",
    "attachment_from_dict",
    "attachment_to_dict",
    "decode_attachment_text",
    "limits_from_project_settings",
    "render_attachments_for_harness",
    "render_for_claude_code",
    "render_for_codex_cli",
    "render_for_direct_chat",
    "render_for_echo",
    "render_for_gemini_cli",
    "render_plan_from_dict",
    "render_plan_to_dict",
]
