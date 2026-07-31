# ruff: noqa: E402, F401, F403, F405
"""Immutable integration snapshots selected by provider-native sessions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from typing import Any

from gigaloom.integration_installer import (
    InstallationPlan,
    TransactionalIntegrationInstaller,
)
from gigaloom.integration_packages import InstallationScope
from gigaloom.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock
from gigaloom.sessions import store as _session_store

utc_now = _session_store.utc_now


INTEGRATION_RUNTIME_SCHEMA_VERSION = 1
MAX_RUNTIME_FILES = 256
MAX_RUNTIME_FILE_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_TOTAL_BYTES = 64 * 1024 * 1024
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_RE = re.compile(r"isnap_[0-9a-f]{32}\Z")
_TRANSACTION_RE = re.compile(r"txn_[0-9a-f]{32}\Z")
_SAFE_MODES = frozenset({0o600, 0o644, 0o700, 0o755})


class IntegrationRuntimeError(RuntimeError):
    """Base error for immutable integration runtime state."""


class IntegrationRuntimeConflictError(IntegrationRuntimeError):
    """Raised when current ownership or a session binding changed."""


class IntegrationRuntimeStateError(IntegrationRuntimeError):
    """Raised when private runtime state is invalid or unsafe."""


class IntegrationRuntimeActivationError(IntegrationRuntimeError):
    """Raised when target-owned discovery or behavior proof fails."""


@dataclass(frozen=True)
class IntegrationRuntimeFile:
    """One private immutable regular file in a runtime snapshot."""

    relative_path: str
    sha256: str
    mode: int
    content: bytes


@dataclass(frozen=True)
class IntegrationRuntimeSnapshot:
    """Exact installed package bytes available to selected native sessions."""

    id: str
    snapshot_hash: str
    package_id: str
    package_version: str
    manifest_sha256: str
    target_id: str
    scope: InstallationScope
    owner_id: str
    owner_key: str
    root_identity: str
    owner_revision: str
    source_transaction_id: str
    previous_snapshot_id: str | None
    created_at: str
    files: tuple[IntegrationRuntimeFile, ...]

    def public_ref(self) -> dict[str, Any]:
        """Return a content-free integrity reference for clients and sessions."""
        return {
            "schema_version": INTEGRATION_RUNTIME_SCHEMA_VERSION,
            "snapshot_id": self.id,
            "snapshot_hash": self.snapshot_hash,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "manifest_sha256": self.manifest_sha256,
            "target_id": self.target_id,
            "scope": self.scope.value,
            "source_transaction_id": self.source_transaction_id,
            "previous_snapshot_id": self.previous_snapshot_id,
            "file_count": len(self.files),
            "created_at": self.created_at,
            "content_free": True,
        }


@dataclass(frozen=True)
class IntegrationRuntimeProbeResult:
    """Content-free target-owned discovery and behavior evidence."""

    discovered: bool
    behavior_verified: bool
    surface: str

    def __post_init__(self) -> None:
        _validate_identity(self.surface, field_name="runtime probe surface")


@dataclass(frozen=True)
class IntegrationRuntimeBinding:
    """Immutable binding between one selected session and one snapshot."""

    session_id: str
    harness_id: str
    snapshot_id: str
    snapshot_hash: str
    owner_key: str
    home: str
    forked_from_session_id: str | None
    discovery_status: str
    behavior_status: str
    probe_surface: str
    bound_at: str

    def public_projection(self) -> dict[str, Any]:
        """Return bounded session activation evidence."""
        return {
            "session_id": self.session_id,
            "harness_id": self.harness_id,
            "snapshot_id": self.snapshot_id,
            "snapshot_hash": self.snapshot_hash,
            "forked_from_session_id": self.forked_from_session_id,
            "discovery_status": self.discovery_status,
            "behavior_status": self.behavior_status,
            "probe_surface": self.probe_surface,
            "bound_at": self.bound_at,
            "content_free": True,
        }


IntegrationRuntimeProbe = Callable[
    [Path, IntegrationRuntimeSnapshot], IntegrationRuntimeProbeResult
]


def _validate_identity(value: str, *, field_name: str) -> None:
    if not _IDENTITY_RE.fullmatch(value):
        raise IntegrationRuntimeStateError(f"{field_name} is invalid")


__all__ = [name for name in globals() if not name.startswith("__")]
