"""Review models primitives."""

from __future__ import annotations

from dataclasses import dataclass


MAX_SUMMARY_CHARS = 1200


MAX_TEST_OUTPUT_CHARS = 4000


@dataclass(frozen=True)
class RunPrArtifact:
    """Local PR artifact generated from one stored harness run."""

    run_id: str
    session_id: str
    title: str
    body: str
    patch: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    test_output: str | None
    branch_name_suggestion: str
    applied_branch: str | None = None
