"""Provider-neutral environment snapshot contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import re
from typing import Any, Callable, Mapping, Protocol

from gigaloom.registries import (
    EntryPointFamily,
)

ENVIRONMENT_SNAPSHOT_SCHEMA_VERSION = 1


NEUTRAL_ENVIRONMENT_ENTRY_POINT_GROUP = "gigaloom.environment_providers.v1"


ENVIRONMENT_PROVIDER_ENTRY_POINTS = EntryPointFamily(
    registry_id="environment_provider",
    api_version=1,
    primary_group=NEUTRAL_ENVIRONMENT_ENTRY_POINT_GROUP,
)


MAX_DISCOVERY_ERRORS = 20


MAX_DISCOVERY_ERROR_CHARS = 400


MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024


MAX_DIFF_HASH_BYTES = 32 * 1024 * 1024


MAX_CHANGED_PATHS = 100


MAX_CAPTURED_PATHS = 512


MAX_PATH_CHARS = 512


GIT_TIMEOUT_SECONDS = 10.0


_HEX_SHA_RE = re.compile(r"[0-9a-f]{40,64}\Z")


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+@~-]{0,127}\Z")


_HOST_RE = re.compile(r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


_REPOSITORY_PART_RE = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")


_SECRET_FILENAMES = frozenset(
    {
        ".netrc",
        "auth.json",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ed25519",
        "id_rsa",
        "token.json",
    }
)


_SECRET_SUFFIXES = (".jks", ".key", ".keystore", ".p12", ".pem", ".pfx")


def _validate_identity(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_bounded_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"environment {field_name} is invalid")


class EnvironmentCaptureError(RuntimeError):
    """Fail-closed bounded reason an environment snapshot could not be captured."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HostedRepositoryHint:
    """Credential-free hosted repository identity derived by the Git owner."""

    host: str
    name_with_owner: str

    def __post_init__(self) -> None:
        if _HOST_RE.fullmatch(self.host) is None:
            raise ValueError("hosted repository host is invalid")
        parts = self.name_with_owner.split("/")
        if len(parts) != 2 or any(
            _REPOSITORY_PART_RE.fullmatch(part) is None for part in parts
        ):
            raise ValueError("hosted repository identity is invalid")


@dataclass(frozen=True)
class EnvironmentProviderDescriptor:
    """Versioned declaration for one environment projection provider."""

    id: str
    display_name: str
    capabilities: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported environment provider schema_version")
        _validate_identity(self.id, "environment provider id")
        if not self.display_name.strip():
            raise ValueError("environment provider display_name is required")
        capabilities = tuple(sorted(set(self.capabilities)))
        if not capabilities:
            raise ValueError("environment provider capabilities are required")
        for capability in capabilities:
            _validate_identity(capability, "environment provider capability")
        object.__setattr__(self, "capabilities", capabilities)


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """Canonical content-free identity of one local Git worktree state."""

    provider_id: str
    repository_root: str
    worktree_root: str
    branch: str | None
    detached: bool
    head: str | None
    base_identity: str | None
    upstream: str | None
    ahead: int
    behind: int
    remote: str | None
    staged_count: int
    unstaged_count: int
    untracked_count: int
    additions: int
    deletions: int
    changed_paths: tuple[str, ...]
    changed_paths_truncated: bool
    diff_sha256: str
    captured_at: str
    push_ready: bool
    push_blocker: str | None
    schema_version: int = ENVIRONMENT_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ENVIRONMENT_SNAPSHOT_SCHEMA_VERSION:
            raise ValueError("unsupported environment snapshot schema_version")
        _validate_identity(self.provider_id, "environment provider id")
        for value, name in (
            (self.repository_root, "repository_root"),
            (self.worktree_root, "worktree_root"),
        ):
            if not value or len(value) > 4096:
                raise ValueError(f"environment {name} is invalid")
        if self.branch is not None:
            _validate_bounded_text(self.branch, "branch")
        if not isinstance(self.detached, bool):
            raise ValueError("environment detached must be a boolean")
        for value, name in (
            (self.head, "head"),
            (self.base_identity, "base_identity"),
        ):
            if value is not None and _HEX_SHA_RE.fullmatch(value) is None:
                raise ValueError(f"environment {name} is invalid")
        for value, name in (
            (self.upstream, "upstream"),
            (self.remote, "remote"),
            (self.push_blocker, "push_blocker"),
        ):
            if value is not None:
                _validate_bounded_text(value, name)
        for name in (
            "ahead",
            "behind",
            "staged_count",
            "unstaged_count",
            "untracked_count",
            "additions",
            "deletions",
        ):
            if not isinstance(getattr(self, name), int) or getattr(self, name) < 0:
                raise ValueError(f"environment {name} must be a non-negative integer")
        paths = tuple(self.changed_paths)
        if len(paths) > MAX_CHANGED_PATHS or paths != tuple(sorted(set(paths))):
            raise ValueError("environment changed_paths are not canonical")
        if any(not _is_safe_summary_path(path) for path in paths):
            raise ValueError("environment changed_paths contain an unsafe path")
        object.__setattr__(self, "changed_paths", paths)
        if not isinstance(self.changed_paths_truncated, bool):
            raise ValueError("changed_paths_truncated must be a boolean")
        if (
            len(self.diff_sha256) != 64
            or _HEX_SHA_RE.fullmatch(self.diff_sha256) is None
        ):
            raise ValueError("environment diff_sha256 is invalid")
        _parse_timestamp(self.captured_at)
        if not isinstance(self.push_ready, bool):
            raise ValueError("environment push_ready must be a boolean")
        if self.push_ready == (self.push_blocker is not None):
            raise ValueError("environment push readiness is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the strict forward-only snapshot shape."""
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "repository_root": self.repository_root,
            "worktree_root": self.worktree_root,
            "branch": self.branch,
            "detached": self.detached,
            "head": self.head,
            "base_identity": self.base_identity,
            "upstream": self.upstream,
            "ahead": self.ahead,
            "behind": self.behind,
            "remote": self.remote,
            "staged_count": self.staged_count,
            "unstaged_count": self.unstaged_count,
            "untracked_count": self.untracked_count,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_paths": list(self.changed_paths),
            "changed_paths_truncated": self.changed_paths_truncated,
            "diff_sha256": self.diff_sha256,
            "captured_at": self.captured_at,
            "push_ready": self.push_ready,
            "push_blocker": self.push_blocker,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EnvironmentSnapshot:
        """Parse only the current exact schema; future shapes fail closed."""
        if not isinstance(payload, Mapping):
            raise ValueError("environment snapshot must be an object")
        expected = {
            "schema_version",
            "provider_id",
            "repository_root",
            "worktree_root",
            "branch",
            "detached",
            "head",
            "base_identity",
            "upstream",
            "ahead",
            "behind",
            "remote",
            "staged_count",
            "unstaged_count",
            "untracked_count",
            "additions",
            "deletions",
            "changed_paths",
            "changed_paths_truncated",
            "diff_sha256",
            "captured_at",
            "push_ready",
            "push_blocker",
        }
        if set(payload) != expected:
            raise ValueError("environment snapshot fields are invalid")
        paths = payload["changed_paths"]
        if not isinstance(paths, list) or any(
            not isinstance(item, str) for item in paths
        ):
            raise ValueError("environment changed_paths must be a string list")
        return cls(**{**payload, "changed_paths": tuple(paths)})


class EnvironmentProvider(Protocol):
    """Read-only provider contract for one local or hosted environment family."""

    @property
    def descriptor(self) -> EnvironmentProviderDescriptor: ...

    def snapshot(self, workspace: str | Path) -> EnvironmentSnapshot: ...


@dataclass(frozen=True)
class EnvironmentProviderPlugin:
    """Discoverable provider descriptor and lazy implementation factory."""

    descriptor: EnvironmentProviderDescriptor
    factory: Callable[[], EnvironmentProvider]

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, EnvironmentProviderDescriptor):
            raise ValueError("environment provider descriptor is invalid")
        if not callable(self.factory):
            raise ValueError("environment provider factory must be callable")


def _is_safe_summary_path(value: str) -> bool:
    if not value or len(value) > MAX_PATH_CHARS or "\x00" in value:
        return False
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    lowered = tuple(part.casefold() for part in path.parts)
    if lowered and lowered[0] == "local":
        return False
    for part in lowered:
        if part == ".env" or part.startswith(".env."):
            return False
        if part in _SECRET_FILENAMES or "secret" in part:
            return False
        if part.endswith(_SECRET_SUFFIXES):
            return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("environment captured_at is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError("environment captured_at is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("environment captured_at is invalid")
    return parsed
