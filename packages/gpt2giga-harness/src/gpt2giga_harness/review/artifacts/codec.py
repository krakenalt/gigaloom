"""Review codec primitives."""

from __future__ import annotations

from typing import Any
from .models import RunPrArtifact


def pr_artifact_to_dict(artifact: RunPrArtifact) -> dict[str, Any]:
    """Serialize a PR artifact for metadata, API, and CLI output."""
    return {
        "run_id": artifact.run_id,
        "session_id": artifact.session_id,
        "title": artifact.title,
        "body": artifact.body,
        "patch": artifact.patch,
        "changed_files": list(artifact.changed_files),
        "untracked_files": list(artifact.untracked_files),
        "test_output": artifact.test_output,
        "branch_name_suggestion": artifact.branch_name_suggestion,
        "applied_branch": artifact.applied_branch,
    }
