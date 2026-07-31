"""Gemini target contracts and validation primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
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


GEMINI_EXTENSION_TARGET_ID = "gemini-extension"
GEMINI_EXTENSION_TARGET_REVISION = "1"
GEMINI_EXTENSION_COMMAND_TIMEOUT_SECONDS = 30.0
MAX_GEMINI_EXTENSION_OUTPUT_CHARS = 128_000
MAX_GEMINI_EXTENSION_FILES = 512
MAX_GEMINI_EXTENSION_FILE_BYTES = 16 * 1024 * 1024
MAX_GEMINI_EXTENSION_TOTAL_BYTES = 64 * 1024 * 1024
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_EXTENSION_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9._-]+)?\Z")
_PLAN_RE = re.compile(r"plan_[0-9a-f]{64}\Z")


class GeminiExtensionSourceKind(str, Enum):
    """Documented Gemini extension source families."""

    LOCAL = "local"
    GIT = "git"
    GALLERY = "gallery"


class GeminiExtensionTargetError(RuntimeError):
    """Base error for Gemini extension target operations."""


class GeminiExtensionCommandError(GeminiExtensionTargetError):
    """Raised when a bounded native Gemini command cannot prove its result."""


class GeminiExtensionPolicyError(GeminiExtensionTargetError):
    """Raised when policy or explicit native consent denies an action."""


@dataclass(frozen=True)
class GeminiExtensionSource:
    """One explicit local, immutable Git, or gallery extension source."""

    kind: GeminiExtensionSourceKind
    location: str
    ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GeminiExtensionSourceKind):
            raise ValueError("Gemini extension source kind is invalid")
        if not isinstance(self.location, str) or not self.location.strip():
            raise ValueError("Gemini extension source location is invalid")
        _validate_secret_free(self.location, "Gemini extension source location")
        if self.kind is GeminiExtensionSourceKind.LOCAL:
            if self.ref is not None:
                raise ValueError("local Gemini extension sources cannot use a Git ref")
        elif self.kind is GeminiExtensionSourceKind.GIT:
            if self.ref is None:
                raise ValueError(
                    "Git Gemini extension sources require an immutable ref"
                )
            _validate_identity(self.ref, "Gemini extension source ref")
            object.__setattr__(self, "location", _canonical_git_source(self.location))
        else:
            if self.ref is not None:
                raise ValueError("Gemini gallery entries do not select an install ref")
            object.__setattr__(
                self, "location", _canonical_gallery_source(self.location)
            )


@dataclass(frozen=True)
class GeminiExtensionRequest:
    """One immutable integration projected to an explicit Gemini scope."""

    package: IntegrationPackage
    scope: InstallationScope
    root: Path
    source: GeminiExtensionSource
    extension_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.package, IntegrationPackage):
            raise TypeError("Gemini extension request requires an IntegrationPackage")
        if not isinstance(self.scope, InstallationScope):
            raise ValueError("Gemini extension request scope is invalid")
        if not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))
        if not isinstance(self.source, GeminiExtensionSource):
            raise TypeError("Gemini extension request source is invalid")
        _validate_extension_name(self.extension_name, "Gemini extension name")
        if not any(
            item.type is IntegrationComponentType.EXTENSION
            for item in self.package.components
        ):
            raise ValueError(
                "Gemini extension request package has no extension component"
            )
        if self.scope not in self.package.scopes:
            raise ValueError(
                "Gemini extension request package does not support the scope"
            )
        if not any(
            item.target_id == GEMINI_EXTENSION_TARGET_ID
            for item in self.package.compatibility
        ):
            raise ValueError(
                "Gemini extension request package is not target-compatible"
            )
        if self.source.kind is GeminiExtensionSourceKind.LOCAL:
            if self.package.source_type is not IntegrationSourceType.LOCAL:
                raise ValueError("local Gemini source requires a local package source")
            if _absolute_path(Path(self.package.source)) != _absolute_path(
                Path(self.source.location)
            ):
                raise ValueError("Gemini extension source does not match the package")
        elif self.source.kind is GeminiExtensionSourceKind.GIT:
            if self.package.source_type is not IntegrationSourceType.GIT:
                raise ValueError("Git Gemini source requires a Git package source")
            if _canonical_git_source(self.package.source) != self.source.location:
                raise ValueError("Gemini extension source does not match the package")
            if self.package.immutable_ref != self.source.ref:
                raise ValueError(
                    "Gemini extension ref does not match the immutable package"
                )
        else:
            if (
                self.package.source_type
                is not IntegrationSourceType.PROVIDER_MARKETPLACE
            ):
                raise ValueError("Gemini gallery source requires a marketplace package")
            if _canonical_gallery_source(self.package.source) != self.source.location:
                raise ValueError("Gemini gallery source does not match the package")


@dataclass(frozen=True)
class GeminiExtensionApproval:
    """Explicit authority for one exact provider-native mutation preview."""

    plan_id: str
    authority: str
    native_consent_acknowledged: bool = False
    source_trust_acknowledged: bool = False
    allow_network: bool = False
    allow_user_home: bool = False

    def __post_init__(self) -> None:
        if not _PLAN_RE.fullmatch(self.plan_id):
            raise ValueError("Gemini extension approval plan_id is invalid")
        _validate_identity(self.authority, "Gemini extension approval authority")
        for field_name in (
            "native_consent_acknowledged",
            "source_trust_acknowledged",
            "allow_network",
            "allow_user_home",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(
                    f"Gemini extension approval {field_name} must be a boolean"
                )


@dataclass(frozen=True)
class GeminiExtensionPlan:
    """Content-free preview bound to source, native state, policy, and restart."""

    action: str
    plan_id: str
    package_id: str
    package_version: str
    manifest_sha256: str
    extension_name: str
    source_sha256: str
    scope: InstallationScope
    root: Path
    native_scope: str
    expected_version: str | None
    expected_enabled: bool | None
    expected_source_sha256: str | None
    network_required: bool
    native_consent_required: bool
    source_trust_required: bool
    restart_required: bool
    policy_status: str
    command_ids: tuple[str, ...]


@dataclass(frozen=True)
class GeminiExtensionProbe:
    """Bounded installed-Gemini capability evidence."""

    status: str
    version: str | None
    command: str
    capabilities: tuple[str, ...]
    gallery_automation: str
    evidence: str


@dataclass(frozen=True)
class GeminiExtensionInstallation:
    """Content-free native Gemini extension discovery projection."""

    name: str
    version: str
    source_kind: str
    source_sha256: str
    scope: InstallationScope
    root: Path
    enabled: bool


@dataclass(frozen=True)
class GeminiExtensionHealth:
    """Exact package identity and native source evidence."""

    extension_name: str
    package_id: str
    version: str
    enabled: bool
    exact_version: bool
    exact_source: bool
    status: str


@dataclass(frozen=True)
class GeminiExtensionResult:
    """Content-free terminal evidence for one documented native CLI action."""

    action: str
    status: str
    extension_name: str
    package_id: str
    version: str | None
    scope: InstallationScope
    enabled: bool
    restart_required: bool
    consent_owner: str = "gemini_cli"


@dataclass(frozen=True)
class GeminiExtensionHandoff:
    """Truthful provider-owned transition without undocumented state writes."""

    action: str
    extension_name: str
    command: tuple[str, ...]
    interaction: str
    consent_owner: str
    restart_required: bool
    reason: str


@dataclass(frozen=True)
class GeminiExtensionCommandResult:
    """Bounded subprocess result returned by an injected command runner."""

    returncode: int
    stdout: str
    stderr: str = ""


GeminiExtensionCommandRunner = Callable[
    [tuple[str, ...], Mapping[str, str], Path | None, float],
    GeminiExtensionCommandResult,
]
GeminiExtensionPolicy = Callable[[str, IntegrationPackage, InstallationScope], bool]


GEMINI_EXTENSION_TARGET_DESCRIPTOR = ExtensionTargetDescriptor(
    id=GEMINI_EXTENSION_TARGET_ID,
    revision=GEMINI_EXTENSION_TARGET_REVISION,
    component_types=(IntegrationComponentType.EXTENSION,),
    scopes=(
        InstallationScope.MANAGED_HOME,
        InstallationScope.PROJECT,
        InstallationScope.USER_HOME,
    ),
    capabilities=(
        "documented_cli_install",
        "documented_cli_uninstall",
        "gallery_handoff",
        "git_source",
        "local_source",
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
            id="gemini-extension-documented-surface",
            kind=IntegrationTrustKind.SOURCE,
            status=IntegrationTrustStatus.VERIFIED,
            authority="google-gemini-cli-docs",
            revision="2026-07-19",
        ),
    ),
)


@dataclass(frozen=True)
class _GeminiExecutionContext:
    config_home: Path
    cwd: Path
    native_scope: str


def _canonical_git_source(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Gemini Git extension source must be a credential-free GitHub URL"
        )
    parts = tuple(part for part in parsed.path.split("/") if part)
    if len(parts) != 2:
        raise ValueError("Gemini Git extension source must identify one repository")
    repo = parts[1].removesuffix(".git")
    if not repo or any(not _IDENTITY_RE.fullmatch(part) for part in (parts[0], repo)):
        raise ValueError("Gemini Git extension repository identity is invalid")
    return urlunsplit(("https", "github.com", f"/{parts[0]}/{repo}.git", "", ""))


def _canonical_gallery_source(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"geminicli.com", "www.geminicli.com"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/extensions/")
    ):
        raise ValueError("Gemini gallery source must be a credential-free gallery URL")
    return urlunsplit(("https", "geminicli.com", parsed.path.rstrip("/"), "", ""))


def _absolute_path(path: Path) -> Path:
    return path.expanduser().absolute()


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_extension_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not _EXTENSION_NAME_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def _validate_secret_free(value: str, label: str) -> None:
    redacted = redact_secrets(value)
    if not isinstance(redacted, str) or redacted != value:
        raise ValueError(f"{label} cannot contain secret material")
