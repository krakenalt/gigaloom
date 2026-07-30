# ruff: noqa: E402, F401, F403, F405
"""Durable compensating transactions across supported integration targets."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from uuid import uuid4

from gpt2giga_harness.external_mcp import HARNESS_MANAGED_MCP_TARGET_ID
from gpt2giga_harness.integration_flows import (
    IntegrationFlowError,
    IntegrationFlowService,
    IntegrationFlowStatus,
    _public_plan as _child_public_plan,
)
from gpt2giga_harness.integration_packages import (
    InstallationScope,
    IntegrationComponentType,
    integration_package_semantic_hash,
)
from gpt2giga_harness.portable_skills import (
    CLAUDE_SKILL_TARGET_ID,
    CODEX_SKILL_TARGET_ID,
    GEMINI_SKILL_TARGET_ID,
)
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_GROUP_SCHEMA_VERSION = 1
MAX_INTEGRATION_GROUPS = 200
_GROUP_ID_RE = re.compile(r"group_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_AUTHORITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_PACK_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_PACK_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?\Z")
_SKILL_TARGETS = (
    CODEX_SKILL_TARGET_ID,
    CLAUDE_SKILL_TARGET_ID,
    GEMINI_SKILL_TARGET_ID,
)
_MCP_TARGETS = (
    "codex-mcp",
    "claude-mcp",
    "gemini-mcp",
    HARNESS_MANAGED_MCP_TARGET_ID,
)
_PACK_TARGETS = (
    ("codex", CODEX_SKILL_TARGET_ID, "codex-mcp"),
    ("claude", CLAUDE_SKILL_TARGET_ID, "claude-mcp"),
    ("gemini", GEMINI_SKILL_TARGET_ID, "gemini-mcp"),
    ("harness", None, HARNESS_MANAGED_MCP_TARGET_ID),
)

__all__ = [name for name in globals() if not name.startswith("__")]
