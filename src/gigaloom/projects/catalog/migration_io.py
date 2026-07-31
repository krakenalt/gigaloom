"""Crash-safe private I/O for the project catalog migration."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping


def read_migration_json(path: Path) -> dict[str, Any]:
    """Read a migration document while rejecting duplicate JSON keys."""
    try:
        payload = json.loads(
            path.read_bytes(),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"project catalog migration file is unreadable: {path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError("project catalog migration document must be an object")
    return payload


def write_migration_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically persist one canonical migration document."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = canonical_migration_json(payload) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write_new_migration_json(path: Path, payload: Mapping[str, Any]) -> str:
    """Create an immutable backup or verify an identical existing one."""
    encoded = canonical_migration_json(payload) + b"\n"
    digest = sha256(encoded).hexdigest()
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("project catalog migration backup already differs")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return digest


def migration_file_digest(path: Path) -> str:
    """Hash one persisted migration document."""
    return sha256(path.read_bytes()).hexdigest()


def canonical_migration_json(payload: Mapping[str, Any]) -> bytes:
    """Encode deterministic private migration state."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
