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
from .installer_support import *  # noqa: F403


def _validate_journal(payload: dict[str, Any], transaction_id: str) -> None:
    legacy_required = {
        "schema_version",
        "transaction_id",
        "plan_id",
        "status",
        "package_id",
        "package_version",
        "manifest_sha256",
        "target_id",
        "scope",
        "owner_id",
        "owner_key",
        "root",
        "expected_owner_revision",
        "approval_authority",
        "allow_user_home",
        "mutations",
        "applied_count",
        "rollback_count",
        "owner_revision",
        "failure",
        "created_at",
        "updated_at",
    }
    required = legacy_required | {"operation", "previous_owner"}
    schema_version = payload.get("schema_version")
    if schema_version == INSTALLATION_STATE_SCHEMA_VERSION:
        if set(payload) != legacy_required:
            raise InstallationStateError("installation journal fields are invalid")
        payload["schema_version"] = INSTALLATION_JOURNAL_SCHEMA_VERSION
        payload["operation"] = "install"
        payload["previous_owner"] = None
    elif schema_version == INSTALLATION_JOURNAL_SCHEMA_VERSION:
        if set(payload) != required:
            raise InstallationStateError("installation journal fields are invalid")
    else:
        raise InstallationStateError("installation journal schema is unsupported")
    if set(payload) != required:
        raise InstallationStateError("installation journal fields are invalid")
    if payload["transaction_id"] != transaction_id:
        raise InstallationStateError("installation journal identity is invalid")
    if not _PLAN_RE.fullmatch(str(payload["plan_id"])):
        raise InstallationStateError("installation journal plan is invalid")
    if payload["status"] not in _JOURNAL_STATUSES:
        raise InstallationStateError("installation journal status is invalid")
    for field_name in (
        "package_id",
        "package_version",
        "target_id",
        "owner_id",
        "approval_authority",
    ):
        try:
            _validate_identity(str(payload[field_name]), field_name=field_name)
        except ValueError as exc:
            raise InstallationStateError(
                "installation journal identity is invalid"
            ) from exc
    for field_name in ("manifest_sha256", "owner_key"):
        if not _HASH_RE.fullmatch(str(payload[field_name])):
            raise InstallationStateError("installation journal hash is invalid")
    for field_name in ("expected_owner_revision", "owner_revision"):
        value = payload[field_name]
        if value is not None and not _HASH_RE.fullmatch(str(value)):
            raise InstallationStateError("installation journal revision is invalid")
    try:
        scope = InstallationScope(str(payload["scope"]))
    except ValueError as exc:
        raise InstallationStateError("installation journal scope is invalid") from exc
    root = Path(str(payload["root"]))
    if not root.is_absolute() or _absolute_path(root) != root:
        raise InstallationStateError("installation journal root is invalid")
    mutations = payload["mutations"]
    if (
        not isinstance(mutations, list)
        or not mutations
        or len(mutations) > MAX_INSTALL_MUTATIONS
    ):
        raise InstallationStateError("installation journal mutations are invalid")
    for record in mutations:
        _validate_journal_mutation(record)
    relative_paths = [str(record["relative_path"]) for record in mutations]
    if relative_paths != sorted(set(relative_paths)):
        raise InstallationStateError("installation journal paths are invalid")
    for index, record in enumerate(mutations):
        if record["stage_file"] != f"{index:04d}.payload" or (
            record["existed"] and record["backup_file"] != f"{index:04d}.backup"
        ):
            raise InstallationStateError(
                "installation journal payload order is invalid"
            )
    for field_name in ("applied_count", "rollback_count"):
        value = payload[field_name]
        if not isinstance(value, int) or not 0 <= value <= len(mutations):
            raise InstallationStateError("installation journal counter is invalid")
    if not isinstance(payload["allow_user_home"], bool):
        raise InstallationStateError("installation journal approval is invalid")
    operation = payload["operation"]
    previous_owner = payload["previous_owner"]
    if operation == "install":
        if previous_owner is not None or payload["expected_owner_revision"] is not None:
            raise InstallationStateError("installation journal operation is invalid")
    elif operation == "update":
        if not isinstance(previous_owner, dict):
            raise InstallationStateError("installation prior owner is invalid")
        _validate_owner(previous_owner, str(payload["owner_key"]))
        if previous_owner["revision"] != payload["expected_owner_revision"]:
            raise InstallationStateError("installation prior owner binding is invalid")
        if (
            previous_owner["target_id"] != payload["target_id"]
            or previous_owner["scope"] != payload["scope"]
            or previous_owner["owner_id"] != payload["owner_id"]
            or previous_owner["root"] != payload["root"]
        ):
            raise InstallationStateError("installation prior owner target is invalid")
        previous_paths = tuple(
            str(item["relative_path"]) for item in previous_owner["files"]
        )
        current_paths = tuple(str(item["relative_path"]) for item in mutations)
        if previous_paths != current_paths:
            raise InstallationStateError("installation update paths are invalid")
    else:
        raise InstallationStateError("installation journal operation is invalid")
    expected_owner_key = _json_hash(
        {
            "target_id": str(payload["target_id"]),
            "scope": scope.value,
            "owner_id": str(payload["owner_id"]),
            "root_sha256": _text_hash(str(root)),
        }
    )
    if payload["owner_key"] != expected_owner_key:
        raise InstallationStateError("installation journal owner binding is invalid")
    plan_mutations = tuple(
        InstallationFilePlan(
            relative_path=str(item["relative_path"]),
            current_sha256=(
                str(item["current_sha256"])
                if item["current_sha256"] is not None
                else None
            ),
            current_mode=(
                int(item["current_mode"]) if item["current_mode"] is not None else None
            ),
            desired_sha256=str(item["desired_sha256"]),
            mode=int(item["mode"]),
            changed=bool(item["changed"]),
        )
        for item in mutations
    )
    semantic = _plan_semantic(
        package_id=str(payload["package_id"]),
        package_version=str(payload["package_version"]),
        manifest_sha256=str(payload["manifest_sha256"]),
        target_id=str(payload["target_id"]),
        scope=scope,
        owner_id=str(payload["owner_id"]),
        owner_key=str(payload["owner_key"]),
        root=root,
        expected_owner_revision=(
            str(payload["expected_owner_revision"])
            if payload["expected_owner_revision"] is not None
            else None
        ),
        mutations=plan_mutations,
    )
    expected_plan_id = f"plan_{_json_hash(semantic)}"
    if payload["plan_id"] != expected_plan_id or transaction_id != (
        f"txn_{expected_plan_id[5:37]}"
    ):
        raise InstallationStateError("installation journal plan binding is invalid")
    failure = payload["failure"]
    if failure is not None and (
        not isinstance(failure, dict)
        or set(failure) != {"code", "error_type"}
        or not all(isinstance(value, str) and value for value in failure.values())
    ):
        raise InstallationStateError("installation journal failure is invalid")


def _validate_journal_mutation(record: Any) -> None:
    required = {
        "relative_path",
        "current_sha256",
        "desired_sha256",
        "mode",
        "changed",
        "stage_file",
        "backup_file",
        "existed",
        "current_mode",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise InstallationStateError("installation journal mutation is invalid")
    try:
        _normalize_relative_path(str(record["relative_path"]))
    except ValueError as exc:
        raise InstallationStateError("installation journal path is invalid") from exc
    if not _HASH_RE.fullmatch(str(record["desired_sha256"])):
        raise InstallationStateError("installation journal desired hash is invalid")
    current = record["current_sha256"]
    if current is not None and not _HASH_RE.fullmatch(str(current)):
        raise InstallationStateError("installation journal current hash is invalid")
    if record["mode"] not in _SAFE_MODES or not isinstance(record["changed"], bool):
        raise InstallationStateError("installation journal file metadata is invalid")
    if not re.fullmatch(r"[0-9]{4}\.payload", str(record["stage_file"])):
        raise InstallationStateError("installation journal stage path is invalid")
    existed = record["existed"]
    if not isinstance(existed, bool):
        raise InstallationStateError("installation journal existence is invalid")
    if existed:
        if current is None:
            raise InstallationStateError("installation journal prior hash is missing")
        if not re.fullmatch(r"[0-9]{4}\.backup", str(record["backup_file"])):
            raise InstallationStateError("installation journal backup path is invalid")
        if (
            not isinstance(record["current_mode"], int)
            or not 0 <= record["current_mode"] <= 0o777
        ):
            raise InstallationStateError("installation journal prior mode is invalid")
    elif record["backup_file"] is not None or record["current_mode"] is not None:
        raise InstallationStateError(
            "installation journal absent-file state is invalid"
        )
    elif current is not None:
        raise InstallationStateError("installation journal absent-file hash is invalid")
    changed = (
        current != record["desired_sha256"] or record["current_mode"] != record["mode"]
    )
    if record["changed"] is not changed:
        raise InstallationStateError("installation journal change flag is invalid")


def _validate_owner(payload: dict[str, Any], owner_key: str) -> None:
    required = {
        "schema_version",
        "owner_key",
        "transaction_id",
        "package_id",
        "package_version",
        "manifest_sha256",
        "target_id",
        "scope",
        "owner_id",
        "root",
        "files",
        "installed_at",
        "revision",
    }
    if set(payload) != required:
        raise InstallationStateError("installation ownership fields are invalid")
    if payload["schema_version"] != INSTALLATION_STATE_SCHEMA_VERSION:
        raise InstallationStateError("installation ownership schema is unsupported")
    if payload["owner_key"] != owner_key or not _HASH_RE.fullmatch(owner_key):
        raise InstallationStateError("installation ownership identity is invalid")
    _validate_transaction_id(str(payload["transaction_id"]))
    for field_name in ("package_id", "package_version", "target_id", "owner_id"):
        try:
            _validate_identity(str(payload[field_name]), field_name=field_name)
        except ValueError as exc:
            raise InstallationStateError(
                "installation ownership identity is invalid"
            ) from exc
    if not _HASH_RE.fullmatch(str(payload["manifest_sha256"])):
        raise InstallationStateError("installation ownership hash is invalid")
    try:
        scope = InstallationScope(str(payload["scope"]))
    except ValueError as exc:
        raise InstallationStateError("installation ownership scope is invalid") from exc
    root = Path(str(payload["root"]))
    if not root.is_absolute() or _absolute_path(root) != root:
        raise InstallationStateError("installation ownership root is invalid")
    expected_owner_key = _json_hash(
        {
            "target_id": str(payload["target_id"]),
            "scope": scope.value,
            "owner_id": str(payload["owner_id"]),
            "root_sha256": _text_hash(str(root)),
        }
    )
    if owner_key != expected_owner_key:
        raise InstallationStateError("installation ownership binding is invalid")
    files = payload["files"]
    if not isinstance(files, list) or not files or len(files) > MAX_INSTALL_MUTATIONS:
        raise InstallationStateError("installation ownership files are invalid")
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "relative_path",
            "sha256",
            "mode",
        }:
            raise InstallationStateError("installation ownership file is invalid")
        _normalize_relative_path(str(item["relative_path"]))
        if (
            not _HASH_RE.fullmatch(str(item["sha256"]))
            or item["mode"] not in _SAFE_MODES
        ):
            raise InstallationStateError("installation ownership file hash is invalid")
    relative_paths = [str(item["relative_path"]) for item in files]
    if relative_paths != sorted(set(relative_paths)):
        raise InstallationStateError("installation ownership paths are invalid")
    if not isinstance(payload["installed_at"], str) or not payload["installed_at"]:
        raise InstallationStateError("installation ownership timestamp is invalid")
    revision = str(payload["revision"])
    expected = _json_hash(
        {key: value for key, value in payload.items() if key != "revision"}
    )
    if revision != expected:
        raise InstallationStateError("installation ownership revision is invalid")


__all__ = [name for name in globals() if not name.startswith("__")]
