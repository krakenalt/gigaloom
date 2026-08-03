"""Bounded, content-free discovery of project instruction sources."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Iterable

from .config import load_project_config


INSTRUCTION_DISCOVERY_FORMAT = "gigaloom.instruction-discovery.v1"
DEFAULT_MAX_DISCOVERED_INSTRUCTIONS = 256
DEFAULT_MAX_GIT_PATHS = 20_000
DEFAULT_MAX_INSTRUCTION_FILE_BYTES = 256 * 1024
MAX_INSTRUCTION_PATH_CHARS = 256
_GIT_TIMEOUT_SECONDS = 5


class ProjectInstructionKind(str, Enum):
    """Observable categories without implying shared prompt semantics."""

    AGENT_INSTRUCTION = "agent_instruction"
    PROVIDER_RULE = "provider_rule"
    PROJECT_PROMPT = "project_prompt"
    PROJECT_SELECTOR = "project_selector"


class ProjectInstructionScope(str, Enum):
    """Scope advertised by the owner of one instruction selector."""

    PROJECT = "project"
    DIRECTORY_TREE = "directory_tree"


class InstructionDiscoveryOmissionReason(str, Enum):
    """Why an expected or matched source was not inspected."""

    FILE_TOO_LARGE = "file_too_large"
    LIMIT = "limit"
    NOT_GIT_VISIBLE = "not_git_visible"
    PATH_TOO_LONG = "path_too_long"
    SYMLINK = "symlink"
    UNAVAILABLE = "unavailable"
    UNSAFE_PATH = "unsafe_path"


@dataclass(frozen=True)
class ProjectInstructionSelectorV1:
    """Adapter-owned path selector; it does not materialize any prompt."""

    selector_id: str
    kind: ProjectInstructionKind
    patterns: tuple[str, ...]
    scope: ProjectInstructionScope
    materialization_owner: str
    report_missing: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.selector_id, "selector_id")
        _validate_identifier(self.materialization_owner, "materialization_owner")
        if not self.patterns:
            raise ValueError("instruction selector requires at least one pattern")
        for pattern in self.patterns:
            _validate_selector_pattern(pattern)


@dataclass(frozen=True)
class DiscoveredProjectInstructionV1:
    """Content-free fact about one project-local instruction source."""

    source_id: str
    selector_id: str
    kind: ProjectInstructionKind
    relative_path: str
    scope: ProjectInstructionScope
    scope_path: str
    source_digest: str
    size_bytes: int
    materialization_owner: str

    def to_dict(self) -> dict[str, Any]:
        """Return the stable content-free representation."""
        return {
            "source_id": self.source_id,
            "selector_id": self.selector_id,
            "kind": self.kind.value,
            "relative_path": self.relative_path,
            "scope": self.scope.value,
            "scope_path": self.scope_path,
            "source_digest": self.source_digest,
            "size_bytes": self.size_bytes,
            "materialization_owner": self.materialization_owner,
        }


@dataclass(frozen=True)
class ProjectInstructionDiscoveryOmissionV1:
    """Visible omission produced without following or reading the source."""

    selector_id: str
    relative_path: str | None
    reason: InstructionDiscoveryOmissionReason

    def to_dict(self) -> dict[str, str | None]:
        """Return the stable content-free representation."""
        return {
            "selector_id": self.selector_id,
            "relative_path": self.relative_path,
            "reason": self.reason.value,
        }


@dataclass(frozen=True)
class ProjectInstructionDiscoveryV1:
    """Immutable bounded discovery result for one exact project snapshot."""

    source_revision: str
    discovery_digest: str
    sources: tuple[DiscoveredProjectInstructionV1, ...]
    omissions: tuple[ProjectInstructionDiscoveryOmissionV1, ...]
    scanned_path_count: int
    scanned_paths_truncated: bool
    max_git_paths: int
    max_sources: int
    max_file_bytes: int
    format: str = INSTRUCTION_DISCOVERY_FORMAT
    content_free: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return the stable wire representation without instruction content."""
        return {
            "format": self.format,
            "source_revision": self.source_revision,
            "discovery_digest": self.discovery_digest,
            "sources": [item.to_dict() for item in self.sources],
            "omissions": [item.to_dict() for item in self.omissions],
            "scanned_path_count": self.scanned_path_count,
            "scanned_paths_truncated": self.scanned_paths_truncated,
            "limits": {
                "git_paths": self.max_git_paths,
                "sources": self.max_sources,
                "file_bytes": self.max_file_bytes,
                "path_chars": MAX_INSTRUCTION_PATH_CHARS,
            },
            "content_free": self.content_free,
        }


def discover_project_instructions(
    project_root: str | Path,
    *,
    selectors: Iterable[ProjectInstructionSelectorV1] | None = None,
    max_git_paths: int = DEFAULT_MAX_GIT_PATHS,
    max_sources: int = DEFAULT_MAX_DISCOVERED_INSTRUCTIONS,
    max_file_bytes: int = DEFAULT_MAX_INSTRUCTION_FILE_BYTES,
) -> ProjectInstructionDiscoveryV1:
    """Discover Git-visible project instructions without following symlinks."""
    _validate_positive_bound(max_git_paths, "max_git_paths")
    _validate_positive_bound(max_sources, "max_sources")
    _validate_positive_bound(max_file_bytes, "max_file_bytes")
    root = Path(project_root).expanduser().resolve()
    git_root = Path(_required_git(root, "rev-parse", "--show-toplevel")).resolve()
    if git_root != root:
        raise ValueError("project_root must be the exact Git worktree root")
    source_revision = _required_git(root, "rev-parse", "HEAD")
    visible_paths = _git_visible_paths(root)
    scanned_paths_truncated = len(visible_paths) > max_git_paths
    selected_visible_paths = visible_paths[:max_git_paths]
    visible_set = set(selected_visible_paths)
    selector_items = tuple(
        DEFAULT_PROJECT_INSTRUCTION_SELECTORS if selectors is None else selectors
    )
    _require_unique_selectors(selector_items)
    selector_items += _project_prompt_selectors(root, visible_set)

    matches = _selector_matches(selector_items, selected_visible_paths)
    sources: list[DiscoveredProjectInstructionV1] = []
    omissions: list[ProjectInstructionDiscoveryOmissionV1] = []
    for candidate_index, (selector, relative_path) in enumerate(matches):
        if candidate_index >= max_sources:
            omissions.append(
                ProjectInstructionDiscoveryOmissionV1(
                    selector.selector_id,
                    relative_path,
                    InstructionDiscoveryOmissionReason.LIMIT,
                )
            )
            continue
        source, omission = _inspect_instruction_source(
            root,
            selector,
            relative_path,
            max_file_bytes=max_file_bytes,
        )
        if source is not None:
            sources.append(source)
        elif omission is not None:
            omissions.append(omission)

    if scanned_paths_truncated:
        omissions.append(
            ProjectInstructionDiscoveryOmissionV1(
                selector_id="discovery.git_visible_paths",
                relative_path=None,
                reason=InstructionDiscoveryOmissionReason.LIMIT,
            )
        )
    normalized_sources = tuple(sorted(sources, key=lambda item: item.source_id))
    normalized_omissions = tuple(
        sorted(
            omissions,
            key=lambda item: (
                item.selector_id,
                item.relative_path or "",
                item.reason.value,
            ),
        )
    )
    return ProjectInstructionDiscoveryV1(
        source_revision=source_revision,
        discovery_digest=_discovery_digest(
            source_revision=source_revision,
            sources=normalized_sources,
            omissions=normalized_omissions,
            scanned_paths_truncated=scanned_paths_truncated,
        ),
        sources=normalized_sources,
        omissions=normalized_omissions,
        scanned_path_count=min(len(visible_paths), max_git_paths),
        scanned_paths_truncated=scanned_paths_truncated,
        max_git_paths=max_git_paths,
        max_sources=max_sources,
        max_file_bytes=max_file_bytes,
    )


def _project_prompt_selectors(
    root: Path,
    visible_paths: set[str],
) -> tuple[ProjectInstructionSelectorV1, ...]:
    config_path = ".giga/harness.toml"
    if config_path not in visible_paths:
        return ()
    absolute = root / config_path
    try:
        if os.path.islink(absolute) or not absolute.is_file():
            return ()
        config = load_project_config(root)
    except (OSError, ValueError):
        return ()
    selectors: list[ProjectInstructionSelectorV1] = []
    if any(preset.prompt is not None for preset in config.presets.values()):
        selectors.append(
            ProjectInstructionSelectorV1(
                selector_id="gigaloom.project_prompt_selectors",
                kind=ProjectInstructionKind.PROJECT_SELECTOR,
                patterns=(config_path,),
                scope=ProjectInstructionScope.PROJECT,
                materialization_owner="gigaloom_projects",
            )
        )
    for preset_name, preset in sorted(config.presets.items()):
        if preset.prompt_file is None:
            continue
        path = _safe_relative_path(preset.prompt_file)
        if path is None:
            continue
        selectors.append(
            ProjectInstructionSelectorV1(
                selector_id=f"gigaloom.preset.{preset_name}",
                kind=ProjectInstructionKind.PROJECT_PROMPT,
                patterns=(path,),
                scope=ProjectInstructionScope.PROJECT,
                materialization_owner="gigaloom_projects",
                report_missing=True,
            )
        )
    return tuple(selectors)


def _selector_matches(
    selectors: tuple[ProjectInstructionSelectorV1, ...],
    visible_paths: tuple[str, ...],
) -> tuple[tuple[ProjectInstructionSelectorV1, str], ...]:
    matches: dict[tuple[str, str], tuple[ProjectInstructionSelectorV1, str]] = {}
    visible_set = set(visible_paths)
    for selector in selectors:
        for pattern in selector.patterns:
            if not any(character in pattern for character in "*?["):
                if pattern in visible_set or selector.report_missing:
                    matches[(selector.selector_id, pattern)] = (selector, pattern)
                continue
            for relative_path in visible_paths:
                if fnmatch.fnmatchcase(relative_path, pattern):
                    matches[(selector.selector_id, relative_path)] = (
                        selector,
                        relative_path,
                    )
    return tuple(matches[key] for key in sorted(matches))


def _inspect_instruction_source(
    root: Path,
    selector: ProjectInstructionSelectorV1,
    relative_path: str,
    *,
    max_file_bytes: int,
) -> tuple[
    DiscoveredProjectInstructionV1 | None,
    ProjectInstructionDiscoveryOmissionV1 | None,
]:
    normalized = _safe_relative_path(relative_path)
    if normalized is None:
        return None, _omission(selector, relative_path, "unsafe_path")
    if len(normalized) > MAX_INSTRUCTION_PATH_CHARS:
        return None, _omission(selector, normalized, "path_too_long")
    path = root / normalized
    try:
        stat = path.lstat()
    except OSError:
        return None, _omission(selector, normalized, "not_git_visible")
    if os.path.islink(path):
        return None, _omission(selector, normalized, "symlink")
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not path.is_file():
        return None, _omission(selector, normalized, "unavailable")
    if stat.st_size > max_file_bytes:
        return None, _omission(selector, normalized, "file_too_large")
    try:
        payload = path.read_bytes()
    except OSError:
        return None, _omission(selector, normalized, "unavailable")
    scope_path = (
        PurePosixPath(normalized).parent.as_posix()
        if selector.scope is ProjectInstructionScope.DIRECTORY_TREE
        else "."
    )
    if scope_path == ".":
        scope_path = ""
    source_key = f"{selector.selector_id}\0{normalized}".encode()
    return (
        DiscoveredProjectInstructionV1(
            source_id=f"pins_{hashlib.sha256(source_key).hexdigest()[:24]}",
            selector_id=selector.selector_id,
            kind=selector.kind,
            relative_path=normalized,
            scope=selector.scope,
            scope_path=scope_path,
            source_digest=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            materialization_owner=selector.materialization_owner,
        ),
        None,
    )


def _omission(
    selector: ProjectInstructionSelectorV1,
    relative_path: str,
    reason: str,
) -> ProjectInstructionDiscoveryOmissionV1:
    return ProjectInstructionDiscoveryOmissionV1(
        selector.selector_id,
        relative_path,
        InstructionDiscoveryOmissionReason(reason),
    )


def _git_visible_paths(root: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            (
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ),
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError("project instruction discovery requires git ls-files") from exc
    try:
        decoded = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("git-visible project paths must be UTF-8") from exc
    paths = {
        normalized
        for item in decoded.split("\0")
        if item and (normalized := _safe_relative_path(item)) is not None
    }
    return tuple(sorted(paths))


def _required_git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "project instruction discovery requires a Git worktree"
        ) from exc
    value = completed.stdout.strip()
    if not value:
        raise ValueError("Git returned an empty project identity")
    return value


def _safe_relative_path(value: str) -> str | None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\x00" in value:
        return None
    return path.as_posix()


def _validate_selector_pattern(pattern: str) -> None:
    path = PurePosixPath(pattern)
    if (
        not pattern
        or len(pattern) > MAX_INSTRUCTION_PATH_CHARS
        or path.is_absolute()
        or ".." in path.parts
        or "\x00" in pattern
    ):
        raise ValueError("instruction selector patterns must stay within the project")


def _validate_identifier(value: str, field_name: str) -> None:
    if not value or len(value) > 256 or any(char in value for char in "\x00\r\n"):
        raise ValueError(f"{field_name} must be 1..256 safe characters")


def _validate_positive_bound(value: int, field_name: str) -> None:
    if value < 1:
        raise ValueError(f"{field_name} must be positive")


def _require_unique_selectors(
    selectors: tuple[ProjectInstructionSelectorV1, ...],
) -> None:
    ids = tuple(item.selector_id for item in selectors)
    if len(ids) != len(set(ids)):
        raise ValueError("instruction selector ids must be unique")


def _discovery_digest(
    *,
    source_revision: str,
    sources: tuple[DiscoveredProjectInstructionV1, ...],
    omissions: tuple[ProjectInstructionDiscoveryOmissionV1, ...],
    scanned_paths_truncated: bool,
) -> str:
    payload = {
        "format": INSTRUCTION_DISCOVERY_FORMAT,
        "source_revision": source_revision,
        "sources": [item.to_dict() for item in sources],
        "omissions": [item.to_dict() for item in omissions],
        "scanned_paths_truncated": scanned_paths_truncated,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


DEFAULT_PROJECT_INSTRUCTION_SELECTORS = (
    ProjectInstructionSelectorV1(
        selector_id="agent.agents_md",
        kind=ProjectInstructionKind.AGENT_INSTRUCTION,
        patterns=("AGENTS.md", "**/AGENTS.md"),
        scope=ProjectInstructionScope.DIRECTORY_TREE,
        materialization_owner="agent_adapter",
    ),
    ProjectInstructionSelectorV1(
        selector_id="provider.claude_project",
        kind=ProjectInstructionKind.PROVIDER_RULE,
        patterns=(
            "CLAUDE.md",
            "**/CLAUDE.md",
            ".claude/rules/*.md",
            ".claude/rules/**/*.md",
        ),
        scope=ProjectInstructionScope.DIRECTORY_TREE,
        materialization_owner="claude_adapter",
    ),
    ProjectInstructionSelectorV1(
        selector_id="provider.gemini_project",
        kind=ProjectInstructionKind.PROVIDER_RULE,
        patterns=("GEMINI.md", "**/GEMINI.md"),
        scope=ProjectInstructionScope.DIRECTORY_TREE,
        materialization_owner="gemini_adapter",
    ),
    ProjectInstructionSelectorV1(
        selector_id="provider.cursor_project",
        kind=ProjectInstructionKind.PROVIDER_RULE,
        patterns=(".cursor/rules/*.mdc", ".cursor/rules/**/*.mdc"),
        scope=ProjectInstructionScope.PROJECT,
        materialization_owner="cursor_adapter",
    ),
)


__all__ = [
    "DEFAULT_MAX_DISCOVERED_INSTRUCTIONS",
    "DEFAULT_MAX_GIT_PATHS",
    "DEFAULT_MAX_INSTRUCTION_FILE_BYTES",
    "DEFAULT_PROJECT_INSTRUCTION_SELECTORS",
    "INSTRUCTION_DISCOVERY_FORMAT",
    "MAX_INSTRUCTION_PATH_CHARS",
    "DiscoveredProjectInstructionV1",
    "InstructionDiscoveryOmissionReason",
    "ProjectInstructionDiscoveryOmissionV1",
    "ProjectInstructionDiscoveryV1",
    "ProjectInstructionKind",
    "ProjectInstructionScope",
    "ProjectInstructionSelectorV1",
    "discover_project_instructions",
]
