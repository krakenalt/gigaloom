"""Constants for the agents subcontext."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping


AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


AGENT_SCHEMA_VERSION = 1


AGENT_DIRECTORY = Path(".giga") / "agents"


ALLOWED_MODES = {"plan", "read", "edit"}


ALLOWED_WORKSPACE_POLICIES = {"auto", "current", "worktree", "temp_copy"}


SECRET_KEY_PARTS = ("secret", "token", "password", "api_key", "apikey", "credential")


NON_SECRET_PROFILE_KEYS = {"max_tokens"}


STARTER_AGENT_PROFILES: Mapping[str, Mapping[str, Any]] = {
    "planner": {
        "title": "Planner",
        "mode": "plan",
        "instructions": "Create a concise, evidence-backed implementation plan before changes.",
    },
    "explorer": {
        "title": "Explorer",
        "mode": "read",
        "instructions": "Explore the project and report concrete code paths, constraints, and risks.",
    },
    "implementer": {
        "title": "Implementer",
        "mode": "edit",
        "workspace_policy": "worktree",
        "instructions": "Implement the requested change in the smallest safe slice and verify it.",
    },
    "reviewer": {
        "title": "Reviewer",
        "mode": "read",
        "instructions": "Review changes for bugs, regressions, security risks, and missing tests.",
    },
    "test-runner": {
        "title": "Test Runner",
        "mode": "read",
        "instructions": "Run focused verification, diagnose failures, and report reproducible evidence.",
    },
    "release-assistant": {
        "title": "Release Assistant",
        "mode": "plan",
        "instructions": "Prepare release notes, compatibility checks, and a safe release checklist.",
    },
}
