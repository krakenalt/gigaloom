"""Private filesystem primitives for managed-agent transactions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Mapping
from uuid import uuid4

from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError


MAX_INSTALLATION_METADATA_BYTES = 256 * 1024


def ensure_private_directory(path: Path) -> None:
    """Create a private directory while refusing symlink substitution."""
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise AgentInstallError("managed_path_not_private_directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
        raise AgentInstallError("managed_path_not_private_directory")
    try:
        path.chmod(0o700)
    except OSError as error:
        raise AgentInstallError("managed_path_permissions_failed") from error


def require_within(path: Path, root: Path, *, reason_code: str) -> Path:
    """Resolve a proposed managed path beneath an explicit authority root."""
    resolved_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise AgentInstallError(reason_code)
    current = resolved_root
    relative = resolved.relative_to(resolved_root)
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise AgentInstallError(reason_code)
    return resolved


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    """Atomically write bounded canonical private JSON."""
    payload = (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()
    if len(payload) > MAX_INSTALLATION_METADATA_BYTES:
        raise AgentInstallError("installation_metadata_too_large")
    ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise AgentInstallError("installation_metadata_write_failed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Mapping[str, object]:
    """Read one bounded regular private JSON object without following symlinks."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= MAX_INSTALLATION_METADATA_BYTES
        ):
            raise AgentInstallError("installation_metadata_invalid")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(MAX_INSTALLATION_METADATA_BYTES + 1)
    except AgentInstallError:
        raise
    except OSError as error:
        raise AgentInstallError("installation_metadata_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentInstallError("installation_metadata_invalid") from error
    if not isinstance(value, dict):
        raise AgentInstallError("installation_metadata_invalid")
    return value
