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

from gpt2giga_harness.integration_packages import (
    InstallationScope,
    IntegrationPackage,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_semantic_hash,
)
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock
from gpt2giga_harness.sessions import store as _session_store

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


def _normalize_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("installation relative path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("installation relative path is invalid")
    normalized = path.as_posix()
    if len(normalized) > 512:
        raise ValueError("installation relative path is too long")
    return normalized


def _validate_identity(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTITY_RE.fullmatch(value):
        raise ValueError(f"{field_name} is invalid")


def _validate_transaction_id(value: str) -> None:
    from .installer_models import InstallationStateError

    if not _TRANSACTION_RE.fullmatch(value):
        raise InstallationStateError("installation transaction id is invalid")


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bytes_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _text_hash(value: str) -> str:
    return _bytes_hash(value.encode("utf-8"))


def _json_hash(value: Any) -> str:
    content = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return _bytes_hash(content)


def stat_mode(path: Path) -> int:
    """Return only portable permission bits for one existing regular file."""
    return path.stat(follow_symlinks=False).st_mode & 0o777


__all__ = [name for name in globals() if not name.startswith("__")]
