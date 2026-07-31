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
from .installer_models import *  # noqa: F403
from .installer_primitives import *  # noqa: F403


def _file_plan_to_dict(plan: InstallationFilePlan) -> dict[str, Any]:
    return {
        "relative_path": plan.relative_path,
        "current_sha256": plan.current_sha256,
        "current_mode": plan.current_mode,
        "desired_sha256": plan.desired_sha256,
        "mode": plan.mode,
        "changed": plan.changed,
    }


def _plan_semantic(
    *,
    package_id: str,
    package_version: str,
    manifest_sha256: str,
    target_id: str,
    scope: InstallationScope,
    owner_id: str,
    owner_key: str,
    root: Path,
    expected_owner_revision: str | None,
    mutations: Sequence[InstallationFilePlan],
) -> dict[str, Any]:
    return {
        "package_id": package_id,
        "package_version": package_version,
        "manifest_sha256": manifest_sha256,
        "target_id": target_id,
        "scope": scope.value,
        "owner_id": owner_id,
        "owner_key": owner_key,
        "root_sha256": _text_hash(str(root)),
        "expected_owner_revision": expected_owner_revision,
        "mutations": [_file_plan_to_dict(item) for item in mutations],
    }


def _owner_key(target: InstallationTarget, root: Path) -> str:
    return _json_hash(
        {
            "target_id": target.id,
            "scope": target.scope.value,
            "owner_id": target.owner_id,
            "root_sha256": _text_hash(str(root)),
        }
    )


def _target_path(
    root: Path, relative_path: str, *, create_parents: bool = False
) -> Path:
    normalized = _normalize_relative_path(relative_path)
    target = root.joinpath(*PurePosixPath(normalized).parts)
    _assert_no_symlink_chain(target, root)
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise InstallationScopeError("installation target must be a regular file")
    if create_parents:
        _mkdir_private_parents(target.parent, root)
    return target


def _read_regular_file(path: Path) -> bytes | None:
    if path.is_symlink():
        raise InstallationScopeError("installation target cannot be a symlink")
    if not path.exists():
        return None
    try:
        if not path.is_file():
            raise InstallationScopeError("installation target must be a regular file")
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(content) > MAX_INSTALL_FILE_BYTES:
        raise InstallationScopeError("installation target file is too large")
    return content


def _assert_no_symlink_chain(path: Path, stop: Path) -> None:
    path = _absolute_path(path)
    stop = _absolute_path(stop)
    if path != stop and not _is_relative_to(path, stop):
        raise InstallationScopeError("installation target escapes its scope")
    current = path
    while True:
        if current.is_symlink():
            raise InstallationScopeError("installation target path contains a symlink")
        if current == stop:
            return
        parent = current.parent
        if parent == current:
            raise InstallationScopeError("installation target escapes its scope")
        current = parent


def _mkdir_private_parents(path: Path, root: Path) -> None:
    if not root.exists():
        if root.is_symlink():
            raise InstallationScopeError("installation target path contains a symlink")
        root.mkdir(parents=True, mode=0o700)
        if root.is_symlink():
            raise InstallationScopeError("installation target path contains a symlink")
        os.chmod(root, 0o700)
    missing = []
    current = path
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    _assert_no_symlink_chain(current, root)
    for item in reversed(missing):
        item.mkdir(mode=0o700)


def _prune_empty_parents(path: Path, root: Path) -> None:
    current = path
    while current != root and _is_relative_to(current, root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _atomic_write_target(path: Path, content: bytes, mode: int) -> None:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    content = (
        json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        + b"\n"
    )
    _atomic_write_private_bytes(path, content)


def _read_private_payload(path: Path, expected_hash: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise InstallationStateError("installation transaction payload is missing")
    content = path.read_bytes()
    if len(content) > MAX_INSTALL_FILE_BYTES or _bytes_hash(content) != expected_hash:
        raise InstallationStateError("installation transaction payload hash is invalid")
    return content


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise InstallationStateError(f"{label} cannot be a symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise InstallationStateError(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise InstallationStateError(f"{label} must be an object")
    return payload


def _bounded_failure(exc: Exception) -> dict[str, str]:
    if isinstance(exc, InstallationVerificationError):
        code = "verification_failed"
    elif isinstance(exc, InstallationConflictError):
        code = "installation_conflict"
    elif isinstance(exc, InstallationStateError):
        code = "state_invalid"
    else:
        code = "apply_failed"
    return {"code": code, "error_type": type(exc).__name__[:128]}


__all__ = [name for name in globals() if not name.startswith("__")]
