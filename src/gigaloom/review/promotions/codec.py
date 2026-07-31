"""Review codec primitives."""

from __future__ import annotations

from typing import Any
from .models import RunPromotionDraft


def promotion_to_dict(draft: RunPromotionDraft) -> dict[str, Any]:
    """Serialize a promotion review."""
    return {
        "kind": draft.kind,
        "target_id": draft.target_id,
        "project_root": draft.project_root,
        "content": draft.content,
        "source_hash": draft.source_hash,
        "redacted_diff": draft.redacted_diff,
        "relative_path": draft.relative_path,
        "review_token": draft.review_token,
        "parameters": dict(draft.parameters),
        "provenance": dict(draft.provenance),
        "warnings": list(draft.warnings),
    }
