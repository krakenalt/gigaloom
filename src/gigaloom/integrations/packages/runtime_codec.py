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
from .runtime_models import *  # noqa: F403


def _capture_files(plan: InstallationPlan) -> tuple[IntegrationRuntimeFile, ...]:
    if not plan.mutations or len(plan.mutations) > MAX_RUNTIME_FILES:
        raise IntegrationRuntimeStateError("runtime snapshot file count is invalid")
    captured: list[IntegrationRuntimeFile] = []
    total = 0
    for mutation in plan.mutations:
        relative_path = _normalize_relative_path(mutation.relative_path)
        path = _runtime_path(plan.root, relative_path)
        if path.is_symlink() or not path.is_file():
            raise IntegrationRuntimeStateError("runtime source file is unsafe")
        mode = stat.S_IMODE(path.stat().st_mode)
        content = path.read_bytes()
        total += len(content)
        if len(content) > MAX_RUNTIME_FILE_BYTES or total > MAX_RUNTIME_TOTAL_BYTES:
            raise IntegrationRuntimeStateError("runtime snapshot payload is too large")
        digest = hashlib.sha256(content).hexdigest()
        if digest != mutation.desired_sha256 or mode != mutation.mode:
            raise IntegrationRuntimeConflictError(
                "installed file changed before runtime snapshot capture"
            )
        captured.append(
            IntegrationRuntimeFile(
                relative_path=relative_path,
                sha256=digest,
                mode=mode,
                content=content,
            )
        )
    return tuple(captured)


def _snapshot_semantic(
    plan: InstallationPlan,
    *,
    owner_revision: str,
    previous_snapshot_id: str | None,
    files: tuple[IntegrationRuntimeFile, ...],
) -> dict[str, Any]:
    return {
        "schema_version": INTEGRATION_RUNTIME_SCHEMA_VERSION,
        "package_id": plan.package_id,
        "package_version": plan.package_version,
        "manifest_sha256": plan.manifest_sha256,
        "target_id": plan.target_id,
        "scope": plan.scope.value,
        "owner_id": plan.owner_id,
        "owner_key": plan.owner_key,
        "root_identity": _root_identity(plan.root),
        "owner_revision": owner_revision,
        "source_transaction_id": plan.transaction_id,
        "previous_snapshot_id": previous_snapshot_id,
        "files": [_file_to_record(item) for item in files],
    }


def _snapshot_to_record(snapshot: IntegrationRuntimeSnapshot) -> dict[str, Any]:
    semantic = {
        "schema_version": INTEGRATION_RUNTIME_SCHEMA_VERSION,
        "package_id": snapshot.package_id,
        "package_version": snapshot.package_version,
        "manifest_sha256": snapshot.manifest_sha256,
        "target_id": snapshot.target_id,
        "scope": snapshot.scope.value,
        "owner_id": snapshot.owner_id,
        "owner_key": snapshot.owner_key,
        "root_identity": snapshot.root_identity,
        "owner_revision": snapshot.owner_revision,
        "source_transaction_id": snapshot.source_transaction_id,
        "previous_snapshot_id": snapshot.previous_snapshot_id,
        "files": [_file_to_record(item) for item in snapshot.files],
    }
    return {
        **semantic,
        "snapshot_id": snapshot.id,
        "snapshot_hash": snapshot.snapshot_hash,
        "created_at": snapshot.created_at,
    }


def _snapshot_from_record(payload: Mapping[str, Any]) -> IntegrationRuntimeSnapshot:
    try:
        if payload.get("schema_version") != INTEGRATION_RUNTIME_SCHEMA_VERSION:
            raise IntegrationRuntimeStateError("runtime snapshot schema is unsupported")
        files_raw = payload.get("files")
        if not isinstance(files_raw, list):
            raise IntegrationRuntimeStateError("runtime snapshot files are invalid")
        files = tuple(_file_from_record(item) for item in files_raw)
        if not files or len(files) > MAX_RUNTIME_FILES:
            raise IntegrationRuntimeStateError("runtime snapshot file count is invalid")
        if sum(len(item.content) for item in files) > MAX_RUNTIME_TOTAL_BYTES:
            raise IntegrationRuntimeStateError("runtime snapshot payload is too large")
        if len({item.relative_path for item in files}) != len(files):
            raise IntegrationRuntimeStateError(
                "runtime snapshot contains duplicate paths"
            )
        _reject_path_collisions(item.relative_path for item in files)
        snapshot = IntegrationRuntimeSnapshot(
            id=str(payload["snapshot_id"]),
            snapshot_hash=str(payload["snapshot_hash"]),
            package_id=str(payload["package_id"]),
            package_version=str(payload["package_version"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            target_id=str(payload["target_id"]),
            scope=InstallationScope(str(payload["scope"])),
            owner_id=str(payload["owner_id"]),
            owner_key=str(payload["owner_key"]),
            root_identity=str(payload["root_identity"]),
            owner_revision=str(payload["owner_revision"]),
            source_transaction_id=str(payload["source_transaction_id"]),
            previous_snapshot_id=(
                str(payload["previous_snapshot_id"])
                if payload.get("previous_snapshot_id") is not None
                else None
            ),
            created_at=str(payload["created_at"]),
            files=files,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationRuntimeStateError("runtime snapshot is invalid") from exc
    _validate_snapshot(snapshot)
    semantic = dict(payload)
    for key in ("snapshot_id", "snapshot_hash", "created_at"):
        semantic.pop(key, None)
    expected_hash = _json_hash(semantic)
    if snapshot.snapshot_hash != expected_hash:
        raise IntegrationRuntimeStateError("runtime snapshot integrity check failed")
    if snapshot.id != f"isnap_{expected_hash[:32]}":
        raise IntegrationRuntimeStateError("runtime snapshot id does not match content")
    return snapshot


def _validate_snapshot(snapshot: IntegrationRuntimeSnapshot) -> None:
    _validate_snapshot_id(snapshot.id)
    _validate_hash(snapshot.snapshot_hash, field_name="runtime snapshot hash")
    _validate_hash(snapshot.manifest_sha256, field_name="runtime manifest hash")
    _validate_hash(snapshot.owner_key, field_name="runtime owner key")
    _validate_hash(snapshot.root_identity, field_name="runtime root identity")
    _validate_hash(snapshot.owner_revision, field_name="runtime owner revision")
    _validate_transaction_id(snapshot.source_transaction_id)
    for field_name in ("package_id", "package_version", "target_id", "owner_id"):
        _validate_identity(getattr(snapshot, field_name), field_name=field_name)
    if snapshot.previous_snapshot_id is not None:
        _validate_snapshot_id(snapshot.previous_snapshot_id)
    if not snapshot.created_at:
        raise IntegrationRuntimeStateError("runtime snapshot timestamp is invalid")


def _file_to_record(file: IntegrationRuntimeFile) -> dict[str, Any]:
    return {
        "relative_path": file.relative_path,
        "sha256": file.sha256,
        "mode": file.mode,
        "content_base64": base64.b64encode(file.content).decode("ascii"),
    }


def _file_from_record(payload: object) -> IntegrationRuntimeFile:
    if not isinstance(payload, Mapping):
        raise IntegrationRuntimeStateError("runtime snapshot file is invalid")
    try:
        relative_path = _normalize_relative_path(str(payload["relative_path"]))
        digest = str(payload["sha256"])
        mode = int(payload["mode"])
        content = base64.b64decode(str(payload["content_base64"]), validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationRuntimeStateError("runtime snapshot file is invalid") from exc
    _validate_hash(digest, field_name="runtime file hash")
    if mode not in _SAFE_MODES or len(content) > MAX_RUNTIME_FILE_BYTES:
        raise IntegrationRuntimeStateError("runtime snapshot file is invalid")
    if hashlib.sha256(content).hexdigest() != digest:
        raise IntegrationRuntimeStateError("runtime snapshot file integrity failed")
    return IntegrationRuntimeFile(relative_path, digest, mode, content)


def _materialize_files(snapshot: IntegrationRuntimeSnapshot, root: Path) -> None:
    for file in snapshot.files:
        path = _runtime_path(root, file.relative_path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        if path.exists() or path.is_symlink():
            raise IntegrationRuntimeStateError("runtime target file already exists")
        with path.open("xb") as handle:
            handle.write(file.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, file.mode)
    _assert_snapshot_files(snapshot, root)


def _assert_snapshot_files(snapshot: IntegrationRuntimeSnapshot, root: Path) -> None:
    for file in snapshot.files:
        path = _runtime_path(root, file.relative_path)
        if path.is_symlink() or not path.is_file():
            raise IntegrationRuntimeStateError("runtime snapshot file is missing")
        content = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        if hashlib.sha256(content).hexdigest() != file.sha256 or mode != file.mode:
            raise IntegrationRuntimeStateError("runtime snapshot file changed")


def _binding_to_record(binding: IntegrationRuntimeBinding) -> dict[str, Any]:
    return asdict(binding)


def _binding_from_record(payload: object) -> IntegrationRuntimeBinding:
    if not isinstance(payload, Mapping):
        raise IntegrationRuntimeStateError("runtime binding is invalid")
    try:
        binding = IntegrationRuntimeBinding(
            session_id=str(payload["session_id"]),
            harness_id=str(payload["harness_id"]),
            snapshot_id=str(payload["snapshot_id"]),
            snapshot_hash=str(payload["snapshot_hash"]),
            owner_key=str(payload["owner_key"]),
            home=str(payload["home"]),
            forked_from_session_id=(
                str(payload["forked_from_session_id"])
                if payload.get("forked_from_session_id") is not None
                else None
            ),
            discovery_status=str(payload["discovery_status"]),
            behavior_status=str(payload["behavior_status"]),
            probe_surface=str(payload["probe_surface"]),
            bound_at=str(payload["bound_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationRuntimeStateError("runtime binding is invalid") from exc
    for field_name in ("session_id", "harness_id", "probe_surface"):
        _validate_identity(getattr(binding, field_name), field_name=field_name)
    _validate_snapshot_id(binding.snapshot_id)
    _validate_hash(binding.snapshot_hash, field_name="runtime binding hash")
    _validate_hash(binding.owner_key, field_name="runtime binding owner")
    if binding.forked_from_session_id is not None:
        _validate_identity(
            binding.forked_from_session_id,
            field_name="runtime source session id",
        )
    if binding.discovery_status != "verified" or binding.behavior_status != "verified":
        raise IntegrationRuntimeStateError("runtime binding evidence is invalid")
    if not Path(binding.home).is_absolute() or not binding.bound_at:
        raise IntegrationRuntimeStateError("runtime binding location is invalid")
    return binding


def _assert_same_owner(
    snapshot: IntegrationRuntimeSnapshot, plan: InstallationPlan
) -> None:
    if (
        snapshot.owner_key != plan.owner_key
        or snapshot.target_id != plan.target_id
        or snapshot.scope is not plan.scope
        or snapshot.owner_id != plan.owner_id
        or snapshot.root_identity != _root_identity(plan.root)
    ):
        raise IntegrationRuntimeConflictError(
            "runtime update does not match the active integration owner"
        )


def _target_harness(target_id: str) -> str:
    if target_id.startswith("codex-"):
        return "codex-cli"
    if target_id.startswith("claude-"):
        return "claude-code"
    if target_id.startswith("gemini-"):
        return "gemini-cli"
    raise IntegrationRuntimeConflictError(
        "runtime target has no provider-native session mapping"
    )


def _root_identity(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()


def _normalize_relative_path(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise IntegrationRuntimeStateError("runtime relative path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise IntegrationRuntimeStateError("runtime relative path is invalid")
    normalized = path.as_posix()
    if len(normalized) > 1024:
        raise IntegrationRuntimeStateError("runtime relative path is too long")
    return normalized


def _runtime_path(root: Path, relative_path: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise IntegrationRuntimeStateError("runtime file root is unsafe")
    path = root
    for part in PurePosixPath(relative_path).parts:
        path = path / part
        if path.is_symlink():
            raise IntegrationRuntimeStateError("runtime file path is unsafe")
    return path


def _reject_path_collisions(paths: Iterable[str]) -> None:
    normalized = sorted(str(item) for item in paths)
    for index, path in enumerate(normalized[:-1]):
        if normalized[index + 1].startswith(f"{path}/"):
            raise IntegrationRuntimeStateError(
                "runtime snapshot paths contain a file-directory collision"
            )


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise IntegrationRuntimeStateError(f"{label} path is unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IntegrationRuntimeStateError(f"{label} was not found") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationRuntimeStateError(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise IntegrationRuntimeStateError(f"{label} is invalid")
    return payload


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise IntegrationRuntimeStateError("runtime state path is unsafe")
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_identity(value: str, *, field_name: str) -> None:
    if not _IDENTITY_RE.fullmatch(value):
        raise IntegrationRuntimeStateError(f"{field_name} is invalid")


def _validate_hash(value: str, *, field_name: str) -> None:
    if not _HASH_RE.fullmatch(value):
        raise IntegrationRuntimeStateError(f"{field_name} is invalid")


def _validate_snapshot_id(value: str) -> None:
    if not _SNAPSHOT_RE.fullmatch(value):
        raise IntegrationRuntimeStateError("runtime snapshot id is invalid")


def _validate_transaction_id(value: str) -> None:
    if not _TRANSACTION_RE.fullmatch(value):
        raise IntegrationRuntimeStateError("runtime transaction id is invalid")


__all__ = [name for name in globals() if not name.startswith("__")]
