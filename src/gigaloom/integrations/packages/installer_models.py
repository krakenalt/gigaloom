# ruff: noqa: E402, F401, F403, F405
"""Transactional, target-scoped integration installation ownership."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any

from gigaloom.integration_packages import (
    InstallationScope,
    IntegrationPackage,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_semantic_hash,
)
from gigaloom.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock
from gigaloom.sessions import store as _session_store

utc_now = _session_store.utc_now


INSTALLATION_STATE_SCHEMA_VERSION = 1
INSTALLATION_JOURNAL_SCHEMA_VERSION = 2
MAX_INSTALL_MUTATIONS = 256
MAX_INSTALL_FILE_BYTES = 16 * 1024 * 1024
MAX_INSTALL_TOTAL_BYTES = 64 * 1024 * 1024
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_TRANSACTION_RE = re.compile(r"txn_[0-9a-f]{32}\Z")
_PLAN_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_SAFE_MODES = frozenset({0o600, 0o644, 0o700, 0o755})
_JOURNAL_STATUSES = frozenset(
    {"prepared", "applying", "verifying", "committed", "rolling_back", "rolled_back"}
)
from .installer_primitives import *  # noqa: F403


class InstallationError(RuntimeError):
    """Base error for transactional integration installation."""


class InstallationScopeError(InstallationError):
    """Raised when a requested mutation root is outside its admitted scope."""


class InstallationConflictError(InstallationError):
    """Raised for stale previews, active targets, drift, or ownership conflicts."""


class InstallationVerificationError(InstallationError):
    """Raised when the target-specific verifier rejects the installed snapshot."""


class InstallationStateError(InstallationError):
    """Raised when durable installer state is corrupt, unsafe, or unsupported."""


@dataclass(frozen=True)
class FileInstallMutation:
    """One desired regular file, retained only in the caller request."""

    relative_path: str
    content: bytes
    mode: int = 0o600

    def __post_init__(self) -> None:
        normalized = _normalize_relative_path(self.relative_path)
        if not isinstance(self.content, bytes):
            raise TypeError("installation file content must be bytes")
        if len(self.content) > MAX_INSTALL_FILE_BYTES:
            raise ValueError("installation file content is too large")
        if self.mode not in _SAFE_MODES:
            raise ValueError("installation file mode is not allowed")
        object.__setattr__(self, "relative_path", normalized)


@dataclass(frozen=True)
class InstallationTarget:
    """One explicit target root and mutation ownership scope."""

    id: str
    scope: InstallationScope
    root: Path
    owner_id: str

    def __post_init__(self) -> None:
        _validate_identity(self.id, field_name="installation target id")
        _validate_identity(self.owner_id, field_name="installation owner id")
        if not isinstance(self.scope, InstallationScope):
            raise ValueError("installation target scope is invalid")
        if not isinstance(self.root, Path):
            object.__setattr__(self, "root", Path(self.root))


@dataclass(frozen=True)
class InstallationRequest:
    """Frozen package plus target-specific desired files."""

    package: IntegrationPackage
    target: InstallationTarget
    mutations: tuple[FileInstallMutation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.package, IntegrationPackage):
            raise TypeError("installation request requires an IntegrationPackage")
        if not isinstance(self.target, InstallationTarget):
            raise TypeError("installation request target is invalid")
        mutations = tuple(sorted(self.mutations, key=lambda item: item.relative_path))
        if not mutations or len(mutations) > MAX_INSTALL_MUTATIONS:
            raise ValueError("installation request mutation count is invalid")
        if any(not isinstance(item, FileInstallMutation) for item in mutations):
            raise TypeError("installation request mutation is invalid")
        paths = [item.relative_path for item in mutations]
        if len(paths) != len(set(paths)):
            raise ValueError("installation request contains duplicate paths")
        if sum(len(item.content) for item in mutations) > MAX_INSTALL_TOTAL_BYTES:
            raise ValueError("installation request payload is too large")
        object.__setattr__(self, "mutations", mutations)


@dataclass(frozen=True)
class InstallationApproval:
    """Explicit authorization bound to one exact content-free preview."""

    plan_id: str
    authority: str
    allow_user_home: bool = False

    def __post_init__(self) -> None:
        if not _PLAN_RE.fullmatch(self.plan_id):
            raise ValueError("installation approval plan_id is invalid")
        _validate_identity(self.authority, field_name="installation approval authority")
        if not isinstance(self.allow_user_home, bool):
            raise ValueError("installation user-home approval must be a boolean")


@dataclass(frozen=True, order=True)
class InstallationFilePlan:
    """Content-free current and desired hashes for one file mutation."""

    relative_path: str
    current_sha256: str | None
    current_mode: int | None
    desired_sha256: str
    mode: int
    changed: bool


@dataclass(frozen=True)
class InstallationPlan:
    """Deterministic content-free preview bound to current target state."""

    plan_id: str
    transaction_id: str
    package_id: str
    package_version: str
    manifest_sha256: str
    target_id: str
    scope: InstallationScope
    owner_id: str
    owner_key: str
    root: Path
    expected_owner_revision: str | None
    mutations: tuple[InstallationFilePlan, ...]
    changed: bool


@dataclass(frozen=True)
class InstallationResult:
    """Content-free terminal transaction evidence."""

    transaction_id: str
    plan_id: str
    status: str
    package_id: str
    package_version: str
    target_id: str
    scope: InstallationScope
    owner_revision: str | None
    updated_at: str


@dataclass(frozen=True)
class InstalledIntegration:
    """Discovered durable ownership and exact-current readiness."""

    transaction_id: str
    package_id: str
    package_version: str
    manifest_sha256: str
    target_id: str
    scope: InstallationScope
    owner_id: str
    owner_revision: str
    relative_paths: tuple[str, ...]
    installed_at: str
    current: bool


@dataclass(frozen=True)
class InstallationRecoveryResult:
    """Content-free outcome for one reconciled interrupted transaction."""

    transaction_id: str
    outcome: str


InstallationVerifier = Callable[[Path, InstallationPlan], bool]
FaultInjector = Callable[[str, str], None]

__all__ = [name for name in globals() if not name.startswith("__")]
