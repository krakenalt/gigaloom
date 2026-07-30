"""Internal Skill library records and injected Git runner contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

MAX_ROOT_SKILLS = 512
MAX_ROOT_PLUGINS = 128
MAX_GIT_CANDIDATES = 256
MAX_PREVIEW_CHARS = 40_000
GIT_TIMEOUT_SECONDS = 90.0
_GITHUB_PART_RE = re.compile(r"[A-Za-z0-9_.-]+\Z")
_GIT_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+~-]{0,255}\Z")


@dataclass(frozen=True)
class GitCommandResult:
    """Bounded Git subprocess result returned by an injectable runner."""

    returncode: int
    stdout: str
    stderr: str = ""


GitCommandRunner = Callable[[tuple[str, ...], Path | None, float], GitCommandResult]


@dataclass(frozen=True)
class _RootSkill:
    id: str
    name: str
    description: str
    path: Path
    target_ids: tuple[str, ...]
    origin: str


@dataclass(frozen=True)
class _RootPlugin:
    id: str
    name: str
    title: str
    description: str
    version: str
    target_ids: tuple[str, ...]
    origin: str
    invocation: str
    bundled_skills: tuple[str, ...]
    default_prompts: tuple[str, ...]
    repository_url: str | None


@dataclass(frozen=True)
class _GitSnapshot:
    repository_url: str
    requested_ref: str | None
    commit: str
    root: Path
    source_provenance: Mapping[str, Any] | None = None
