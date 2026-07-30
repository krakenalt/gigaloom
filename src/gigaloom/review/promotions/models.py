"""Review models primitives."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping


PROMOTION_KINDS = frozenset({"agent", "workflow", "eval"})


SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


ONE_OFF_ID = re.compile(r"\b(?:run|sess|job|attempt|trace|span)_[A-Za-z0-9_-]+\b")


MAX_PROMPT_CHARS = 8_000


@dataclass(frozen=True)
class RunPromotionDraft:
    """One validated candidate that must be reviewed before apply."""

    kind: str
    target_id: str
    project_root: str
    content: str
    source_hash: str
    redacted_diff: str
    relative_path: str
    review_token: str
    parameters: Mapping[str, Any]
    provenance: Mapping[str, Any]
    warnings: tuple[str, ...]
