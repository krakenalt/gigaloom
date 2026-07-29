"""Typed attachment preparation result for one run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gpt2giga_harness.attachments import AttachmentRenderPlan, HarnessAttachment


@dataclass(frozen=True)
class PreparedAttachments:
    """Loaded attachments and their provider-facing render evidence."""

    attachments: tuple[HarnessAttachment, ...]
    metadata: tuple[dict[str, Any], ...]
    render_plan: AttachmentRenderPlan | None = None
    render_plan_payload: Mapping[str, Any] | None = None
