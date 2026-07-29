"""Review apply primitives."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from gpt2giga_harness.review.ports import ProjectAuthoringService, ProjectFileDraft
from gpt2giga_harness.review.ports import HarnessSessionStore
from .shared import _project_draft, _review_token, _validate_target


def apply_run_promotion(
    store: HarnessSessionStore,
    run_id: str,
    *,
    kind: str,
    target_id: str,
    content: str,
    source_hash: str,
    review_token: str,
) -> tuple[str, ProjectFileDraft[Any]]:
    """Apply only content carrying a matching review token and source ETag."""
    _validate_target(kind, target_id)
    if not review_token or review_token != _review_token(kind, target_id, content):
        raise ValueError("Promotion content must be reviewed again before apply")
    run = store.get_run(run_id)
    if not run.workspace:
        raise ValueError("Run has no project workspace")
    root = Path(run.workspace).expanduser().resolve()
    draft = _project_draft(root, kind, target_id, content, source_hash=source_hash)
    return ProjectAuthoringService(root).apply(draft), draft
