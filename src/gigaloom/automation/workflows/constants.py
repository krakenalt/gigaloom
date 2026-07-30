"""Constants for the workflows subcontext."""

from __future__ import annotations

from pathlib import Path
import re


WORKFLOW_DIRECTORY = Path(".giga") / "workflows"


WORKFLOW_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


MAX_WORKFLOW_STEPS = 64


MAX_FAN_OUT = 16


MAX_HANDOFF_SUMMARY_CHARS = 8_000


MAX_HANDOFF_ARTIFACTS = 16


MAX_HANDOFF_PATCH_PREVIEW_CHARS = 6_000


HANDOFF_ARTIFACT_TYPES = frozenset(
    {
        "plan",
        "selected_files",
        "patch",
        "diff",
        "test_report",
        "review_findings",
        "pr_draft",
    }
)


TERMINAL_STEP_STATUSES = frozenset({"succeeded", "failed", "canceled", "skipped"})


WORKFLOW_COORDINATION_OUTPUT = "_coordination"
