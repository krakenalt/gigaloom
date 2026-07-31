"""Stable soft launch profile value objects."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

PROJECT_LAUNCH_PROFILE_SCHEMA_VERSION = 1
MAX_LAUNCH_PROFILES = 10_000
MAX_LAUNCH_PROFILE_PAGE_SIZE = 100
MAX_LAUNCH_HINT_CHARS = 200

TerminalModeHint = Literal["auto", "managed", "direct"]

_PROFILE_ID_PATTERN = re.compile(r"^launch_[a-f0-9]{24}$")
_CATALOG_ID_PATTERN = re.compile(r"^prj_[a-f0-9]{24}$")
_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class ProjectLaunchProfileV1:
    """Soft project defaults that never grant authority or inject arguments."""

    launch_profile_id: str
    catalog_project_id: str
    display_name: str
    agent_hint: str | None
    structured_route_hint: str | None
    model_hint: str | None
    mode_hint: str | None
    host_hint: str | None
    workspace_policy_hint: str | None
    terminal_mode_hint: TerminalModeHint | None
    revision: int
    digest: str
    schema_version: Literal[1] = PROJECT_LAUNCH_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_LAUNCH_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported launch profile schema_version")
        if not _PROFILE_ID_PATTERN.fullmatch(self.launch_profile_id):
            raise ValueError("invalid launch_profile_id")
        if not _CATALOG_ID_PATTERN.fullmatch(self.catalog_project_id):
            raise ValueError("invalid catalog_project_id")
        _validate_hint(self.display_name, "display_name", required=True)
        for name, value in (
            ("agent_hint", self.agent_hint),
            ("structured_route_hint", self.structured_route_hint),
            ("model_hint", self.model_hint),
            ("mode_hint", self.mode_hint),
            ("host_hint", self.host_hint),
            ("workspace_policy_hint", self.workspace_policy_hint),
        ):
            _validate_hint(value, name)
        if self.terminal_mode_hint not in {None, "auto", "managed", "direct"}:
            raise ValueError("invalid terminal_mode_hint")
        if self.revision < 1:
            raise ValueError("launch profile revision must be positive")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise ValueError("launch profile digest must be a SHA-256 digest")


@dataclass(frozen=True)
class LaunchProfilePageV1:
    """One bounded stable profile page for a catalog project."""

    items: tuple[ProjectLaunchProfileV1, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class LaunchResolutionContextV1:
    """Explicit available values used to resolve soft hints."""

    agent_ids: frozenset[str] = frozenset()
    structured_route_ids: frozenset[str] = frozenset()
    model_ids: frozenset[str] = frozenset()
    modes: frozenset[str] = frozenset()
    host_ids: frozenset[str] = frozenset()
    workspace_policies: frozenset[str] = frozenset()
    terminal_modes: frozenset[str] = frozenset({"direct"})


@dataclass(frozen=True)
class UnsatisfiedLaunchHintV1:
    """One visible soft hint that current inventory cannot satisfy."""

    field: str
    value: str
    reason: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True)
class ResolvedProjectLaunchProfileV1:
    """Previewable launch inputs with visible unsatisfied hints."""

    launch_profile_id: str
    catalog_project_id: str
    profile_digest: str
    agent_id: str | None
    structured_route_id: str | None
    model_id: str | None
    mode: str | None
    host_id: str | None
    workspace_policy: str | None
    terminal_mode: TerminalModeHint | None
    unsatisfied_hints: tuple[UnsatisfiedLaunchHintV1, ...]
    authority_granted: Literal[False] = False


def _validate_hint(value: str | None, name: str, *, required: bool = False) -> None:
    if value is None:
        if required:
            raise ValueError(f"{name} is required")
        return
    if not value or value != value.strip() or len(value) > MAX_LAUNCH_HINT_CHARS:
        raise ValueError(f"{name} is empty, unnormalized, or too long")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} contains control characters")
