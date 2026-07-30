"""Claude target contracts and validation primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit, urlunsplit

from gigaloom.integration_packages import (
    ExtensionTargetDescriptor,
    InstallationScope,
    IntegrationComponentType,
    IntegrationPackage,
    IntegrationSourceType,
    IntegrationTrustEvidence,
    IntegrationTrustKind,
    IntegrationTrustStatus,
)
from gigaloom.types import redact_secrets


CLAUDE_PLUGIN_TARGET_ID = "claude-plugin"
CLAUDE_PLUGIN_TARGET_REVISION = "1"
CLAUDE_PLUGIN_COMMAND_TIMEOUT_SECONDS = 30.0
MAX_CLAUDE_PLUGIN_OUTPUT_CHARS = 128_000
MAX_CLAUDE_PLUGIN_FILES = 512
MAX_CLAUDE_PLUGIN_FILE_BYTES = 16 * 1024 * 1024
MAX_CLAUDE_PLUGIN_TOTAL_BYTES = 64 * 1024 * 1024
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_PLUGIN_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9._-]+)?\Z")
_PLAN_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_GITHUB_SHORTHAND_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


class ClaudePluginSourceKind(str, Enum):
    """Documented Claude marketplace source families admitted by the driver."""

    LOCAL = "local"
    GIT = "git"


class ClaudePluginTargetError(RuntimeError):
    """Base error for Claude plugin target operations."""


class ClaudePluginCommandError(ClaudePluginTargetError):
    """Raised when a bounded native Claude command cannot prove its result."""


class ClaudePluginPolicyError(ClaudePluginTargetError):
    """Raised when policy or explicit native consent denies an action."""


@dataclass(frozen=True)
class ClaudePluginSource:
    """One explicit local or immutable Git Claude marketplace source."""

    marketplace_name: str
    kind: ClaudePluginSourceKind
    location: str
    ref: str | None = None
    sparse: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_plugin_name(self.marketplace_name, "Claude marketplace name")
        if not isinstance(self.kind, ClaudePluginSourceKind):
            raise ValueError("Claude plugin source kind is invalid")
        if not isinstance(self.location, str) or not self.location.strip():
            raise ValueError("Claude plugin source location is invalid")
        _validate_secret_free(self.location, "Claude plugin source location")
        sparse = tuple(sorted(set(self.sparse)))
        if len(sparse) != len(self.sparse) or len(sparse) > 32:
            raise ValueError("Claude plugin sparse paths are invalid")
        for item in sparse:
            _normalize_relative_path(item, label="Claude plugin sparse path")
        object.__setattr__(self, "sparse", sparse)
        if self.kind is ClaudePluginSourceKind.LOCAL:
            if self.ref is not None or sparse:
                raise ValueError("local Claude marketplaces cannot use Git selectors")
            return
        if self.ref is None:
            raise ValueError("Git Claude marketplaces require an immutable ref")
        _validate_identity(self.ref, "Claude plugin source ref")
        object.__setattr__(self, "location", _canonical_git_source(self.location))


@dataclass(frozen=True)
class ClaudePluginRequest:
    """One immutable integration projected to an explicit Claude plugin scope."""

    package: IntegrationPackage
    scope: InstallationScope
    root: Path
    source: ClaudePluginSource
    plugin_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.package, IntegrationPackage):
            raise TypeError("Claude plugin request requires an IntegrationPackage")
        if not isinstance(self.scope, InstallationScope):
            raise ValueError("Claude plugin request scope is invalid")
        if not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))
        if not isinstance(self.source, ClaudePluginSource):
            raise TypeError("Claude plugin request source is invalid")
        _validate_plugin_name(self.plugin_name, "Claude plugin name")
        if not any(
            item.type is IntegrationComponentType.PLUGIN
            for item in self.package.components
        ):
            raise ValueError("Claude plugin request package has no plugin component")
        if self.scope not in self.package.scopes:
            raise ValueError("Claude plugin request package does not support the scope")
        if not any(
            item.target_id == CLAUDE_PLUGIN_TARGET_ID
            for item in self.package.compatibility
        ):
            raise ValueError("Claude plugin request package is not target-compatible")
        if self.source.kind is ClaudePluginSourceKind.LOCAL:
            if self.package.source_type is not IntegrationSourceType.LOCAL:
                raise ValueError("local Claude source requires a local package source")
            if _absolute_path(Path(self.package.source)) != _absolute_path(
                Path(self.source.location)
            ):
                raise ValueError(
                    "Claude plugin source does not match the package source"
                )
        elif self.package.source_type not in {
            IntegrationSourceType.GIT,
            IntegrationSourceType.PROVIDER_MARKETPLACE,
        }:
            raise ValueError("Git Claude source requires a Git or marketplace package")
        elif _canonical_git_source(self.package.source) != self.source.location:
            raise ValueError("Claude plugin source does not match the package source")
        if (
            self.source.kind is ClaudePluginSourceKind.GIT
            and self.package.immutable_ref != self.source.ref
        ):
            raise ValueError(
                "Claude plugin Git ref does not match the immutable package"
            )


@dataclass(frozen=True)
class ClaudePluginApproval:
    """Explicit authority for one exact provider-native mutation preview."""

    plan_id: str
    authority: str
    native_consent_acknowledged: bool = False
    allow_network: bool = False
    allow_user_home: bool = False

    def __post_init__(self) -> None:
        if not _PLAN_RE.fullmatch(self.plan_id):
            raise ValueError("Claude plugin approval plan_id is invalid")
        _validate_identity(self.authority, "Claude plugin approval authority")
        for field_name in (
            "native_consent_acknowledged",
            "allow_network",
            "allow_user_home",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(
                    f"Claude plugin approval {field_name} must be a boolean"
                )


@dataclass(frozen=True)
class ClaudePluginPlan:
    """Content-free preview bound to source, native state, policy, and restart."""

    action: str
    plan_id: str
    package_id: str
    package_version: str
    manifest_sha256: str
    plugin_id: str
    source_sha256: str
    scope: InstallationScope
    root: Path
    native_scope: str
    expected_version: str | None
    expected_enabled: bool | None
    network_required: bool
    native_consent_required: bool
    restart_required: bool
    policy_status: str
    command_ids: tuple[str, ...]


@dataclass(frozen=True)
class ClaudePluginProbe:
    """Bounded installed-Claude capability evidence."""

    status: str
    version: str | None
    command: str
    capabilities: tuple[str, ...]
    evidence: str


@dataclass(frozen=True)
class ClaudePluginInstallation:
    """Content-free native Claude plugin discovery projection."""

    plugin_id: str
    name: str
    marketplace_name: str
    version: str
    scope: InstallationScope
    root: Path
    enabled: bool


@dataclass(frozen=True)
class ClaudePluginHealth:
    """Exact package identity and native marketplace evidence."""

    plugin_id: str
    package_id: str
    version: str
    enabled: bool
    exact_version: bool
    exact_source: bool
    status: str


@dataclass(frozen=True)
class ClaudePluginResult:
    """Content-free terminal evidence for one documented native CLI action."""

    action: str
    status: str
    plugin_id: str
    package_id: str
    version: str | None
    scope: InstallationScope
    enabled: bool
    restart_required: bool
    native_consent_owner: str = "claude"


@dataclass(frozen=True)
class ClaudePluginHandoff:
    """Truthful provider-owned transition without undocumented config writes."""

    action: str
    plugin_id: str
    command: tuple[str, ...]
    interaction: str
    consent_owner: str
    restart_required: bool
    reason: str


@dataclass(frozen=True)
class ClaudePluginCommandResult:
    """Bounded subprocess result returned by an injected command runner."""

    returncode: int
    stdout: str
    stderr: str = ""


ClaudePluginCommandRunner = Callable[
    [tuple[str, ...], Mapping[str, str], Path | None, float],
    ClaudePluginCommandResult,
]
ClaudePluginPolicy = Callable[[str, IntegrationPackage, InstallationScope], bool]


CLAUDE_PLUGIN_TARGET_DESCRIPTOR = ExtensionTargetDescriptor(
    id=CLAUDE_PLUGIN_TARGET_ID,
    revision=CLAUDE_PLUGIN_TARGET_REVISION,
    component_types=(IntegrationComponentType.PLUGIN,),
    scopes=(
        InstallationScope.MANAGED_HOME,
        InstallationScope.PROJECT,
        InstallationScope.USER_HOME,
    ),
    capabilities=(
        "documented_cli_install",
        "documented_cli_uninstall",
        "git_marketplace",
        "local_marketplace",
        "native_discovery",
        "native_enable_disable",
        "native_validation",
        "policy_deny",
        "project_scope",
        "restart_required",
        "rollback_handoff",
        "update",
    ),
    trust_evidence=(
        IntegrationTrustEvidence(
            id="claude-plugin-documented-surface",
            kind=IntegrationTrustKind.SOURCE,
            status=IntegrationTrustStatus.VERIFIED,
            authority="anthropic-claude-code-docs",
            revision="2026-07-19",
        ),
    ),
)


@dataclass(frozen=True)
class _ClaudeExecutionContext:
    config_dir: Path
    cwd: Path | None
    native_scope: str


def _normalize_relative_path(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{label} is invalid")
    candidate = value.removeprefix("./")
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"{label} is invalid")
    return path.as_posix()


def _canonical_git_source(value: str) -> str:
    _validate_secret_free(value, "Claude plugin Git source")
    if _GITHUB_SHORTHAND_RE.fullmatch(value):
        return value
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith(".git")
    ):
        raise ValueError(
            "Claude plugin Git source must be GitHub shorthand or HTTPS .git"
        )
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_plugin_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not _PLUGIN_NAME_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_secret_free(value: str, label: str) -> None:
    if str(redact_secrets(value)) != value:
        raise ValueError(f"{label} contains secret material")
