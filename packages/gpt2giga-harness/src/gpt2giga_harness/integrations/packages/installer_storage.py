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
from .installer_journal import *  # noqa: F403
from .installer_models import *  # noqa: F403
from .installer_primitives import *  # noqa: F403
from .installer_support import *  # noqa: F403


class _InstallerStorageMixin:
    """Implementation slice for the transactional installer."""

    def _apply_staged(self, journal: dict[str, Any]) -> dict[str, Any]:
        journal = self._set_journal_status(journal, "applying")
        root = self._validate_journal_scope(journal)
        transaction_dir = self.transactions_root / str(journal["transaction_id"])
        for index, record in enumerate(journal["mutations"]):
            stage = transaction_dir / "stage" / str(record["stage_file"])
            content = _read_private_payload(stage, str(record["desired_sha256"]))
            target = _target_path(
                root, str(record["relative_path"]), create_parents=True
            )
            _atomic_write_target(target, content, int(record["mode"]))
            journal["applied_count"] = index + 1
            journal["updated_at"] = utc_now()
            self._write_journal(journal)
            self.fault_injector(f"apply:{index + 1}", str(journal["transaction_id"]))
        return journal

    def _rollback_from_journal(self, journal: dict[str, Any]) -> dict[str, Any]:
        journal = self._set_journal_status(journal, "rolling_back")
        root = self._validate_journal_scope(journal)
        transaction_dir = self.transactions_root / str(journal["transaction_id"])
        for index, record in enumerate(reversed(journal["mutations"])):
            target = _target_path(
                root, str(record["relative_path"]), create_parents=True
            )
            if bool(record["existed"]):
                backup_name = record.get("backup_file")
                if not isinstance(backup_name, str):
                    raise InstallationStateError(
                        "installation backup reference is invalid"
                    )
                backup = transaction_dir / "backups" / backup_name
                content = _read_private_payload(backup, str(record["current_sha256"]))
                _atomic_write_target(target, content, int(record["current_mode"]))
            else:
                if target.is_symlink():
                    raise InstallationConflictError(
                        "installation target changed outside the installer"
                    )
                target.unlink(missing_ok=True)
                _prune_empty_parents(target.parent, root)
            journal["rollback_count"] = index + 1
            journal["updated_at"] = utc_now()
            self._write_journal(journal)
            self.fault_injector(f"rollback:{index + 1}", str(journal["transaction_id"]))
        previous_owner = journal.get("previous_owner")
        if isinstance(previous_owner, Mapping):
            self._restore_previous_owner(journal, previous_owner)
            journal["owner_revision"] = str(previous_owner["revision"])
        else:
            self._delete_owner_if_owned(
                str(journal["owner_key"]), str(journal["transaction_id"])
            )
            journal["owner_revision"] = None
        return self._set_journal_status(journal, "rolled_back")

    def _restore_previous_owner(
        self,
        journal: Mapping[str, Any],
        previous_owner: Mapping[str, Any],
    ) -> None:
        owner_key = str(journal["owner_key"])
        current = self._read_owner(owner_key)
        allowed_transactions = {
            str(journal["transaction_id"]),
            str(previous_owner["transaction_id"]),
        }
        if (
            current is not None
            and str(current["transaction_id"]) not in allowed_transactions
        ):
            raise InstallationConflictError(
                "installation ownership changed outside the installer"
            )
        _atomic_write_private_json(self._owner_path(owner_key), previous_owner)

    def _assert_recoverable_files(self, journal: Mapping[str, Any]) -> None:
        root = self._validate_journal_scope(journal)
        for record in journal["mutations"]:
            target = _target_path(root, str(record["relative_path"]))
            current = _read_regular_file(target)
            current_hash = _bytes_hash(current) if current is not None else None
            if current_hash not in {
                record.get("current_sha256"),
                record.get("desired_sha256"),
            }:
                raise InstallationConflictError(
                    "interrupted installation target changed outside the installer"
                )
            if current is not None:
                allowed_modes = {int(record["mode"])}
                if record.get("current_mode") is not None:
                    allowed_modes.add(int(record["current_mode"]))
                if stat_mode(target) not in allowed_modes:
                    raise InstallationConflictError(
                        "interrupted installation target mode changed outside the installer"
                    )

    def _publish_owner(self, journal: Mapping[str, Any]) -> dict[str, Any]:
        files = [
            {
                "relative_path": str(record["relative_path"]),
                "sha256": str(record["desired_sha256"]),
                "mode": int(record["mode"]),
            }
            for record in journal["mutations"]
        ]
        payload = {
            "schema_version": INSTALLATION_STATE_SCHEMA_VERSION,
            "owner_key": str(journal["owner_key"]),
            "transaction_id": str(journal["transaction_id"]),
            "package_id": str(journal["package_id"]),
            "package_version": str(journal["package_version"]),
            "manifest_sha256": str(journal["manifest_sha256"]),
            "target_id": str(journal["target_id"]),
            "scope": str(journal["scope"]),
            "owner_id": str(journal["owner_id"]),
            "root": str(journal["root"]),
            "files": files,
            "installed_at": utc_now(),
        }
        payload["revision"] = _json_hash(payload)
        _atomic_write_private_json(self._owner_path(str(journal["owner_key"])), payload)
        return payload

    def _installed_from_journal(
        self, journal: Mapping[str, Any]
    ) -> InstalledIntegration:
        owner = self._read_owner(str(journal["owner_key"]))
        if owner is None or owner.get("transaction_id") != journal["transaction_id"]:
            raise InstallationStateError("installation ownership record is missing")
        return self._installed_from_owner(owner)

    def _installed_from_owner(self, owner: Mapping[str, Any]) -> InstalledIntegration:
        root = self._validate_scope_root(
            InstallationScope(str(owner["scope"])), Path(str(owner["root"]))
        )
        current = True
        paths = []
        for record in owner["files"]:
            relative_path = str(record["relative_path"])
            paths.append(relative_path)
            try:
                target = _target_path(root, relative_path)
                content = _read_regular_file(target)
            except InstallationError:
                current = False
                continue
            if (
                content is None
                or _bytes_hash(content) != record["sha256"]
                or stat_mode(target) != record["mode"]
            ):
                current = False
        return InstalledIntegration(
            transaction_id=str(owner["transaction_id"]),
            package_id=str(owner["package_id"]),
            package_version=str(owner["package_version"]),
            manifest_sha256=str(owner["manifest_sha256"]),
            target_id=str(owner["target_id"]),
            scope=InstallationScope(str(owner["scope"])),
            owner_id=str(owner["owner_id"]),
            owner_revision=str(owner["revision"]),
            relative_paths=tuple(paths),
            installed_at=str(owner["installed_at"]),
            current=current,
        )

    def _owner_matches_plan(
        self, owner: Mapping[str, Any], plan: InstallationPlan
    ) -> bool:
        return (
            owner.get("package_id") == plan.package_id
            and owner.get("package_version") == plan.package_version
            and owner.get("manifest_sha256") == plan.manifest_sha256
            and owner.get("target_id") == plan.target_id
            and owner.get("scope") == plan.scope.value
            and {
                str(item["relative_path"]): str(item["sha256"])
                for item in owner["files"]
            }
            == {item.relative_path: item.desired_sha256 for item in plan.mutations}
        )

    def _result_from_owner(self, owner: Mapping[str, Any]) -> InstallationResult:
        journal = self._load_journal(str(owner["transaction_id"]))
        return self._result_from_journal(journal)

    def _result_from_journal(self, journal: Mapping[str, Any]) -> InstallationResult:
        return InstallationResult(
            transaction_id=str(journal["transaction_id"]),
            plan_id=str(journal["plan_id"]),
            status=str(journal["status"]),
            package_id=str(journal["package_id"]),
            package_version=str(journal["package_version"]),
            target_id=str(journal["target_id"]),
            scope=InstallationScope(str(journal["scope"])),
            owner_revision=(
                str(journal["owner_revision"])
                if journal.get("owner_revision") is not None
                else None
            ),
            updated_at=str(journal["updated_at"]),
        )

    def _plan_from_journal(self, journal: Mapping[str, Any]) -> InstallationPlan:
        mutations = tuple(
            InstallationFilePlan(
                relative_path=str(item["relative_path"]),
                current_sha256=(
                    str(item["current_sha256"])
                    if item.get("current_sha256") is not None
                    else None
                ),
                current_mode=(
                    int(item["current_mode"])
                    if item.get("current_mode") is not None
                    else None
                ),
                desired_sha256=str(item["desired_sha256"]),
                mode=int(item["mode"]),
                changed=bool(item["changed"]),
            )
            for item in journal["mutations"]
        )
        return InstallationPlan(
            plan_id=str(journal["plan_id"]),
            transaction_id=str(journal["transaction_id"]),
            package_id=str(journal["package_id"]),
            package_version=str(journal["package_version"]),
            manifest_sha256=str(journal["manifest_sha256"]),
            target_id=str(journal["target_id"]),
            scope=InstallationScope(str(journal["scope"])),
            owner_id=str(journal["owner_id"]),
            owner_key=str(journal["owner_key"]),
            root=Path(str(journal["root"])),
            expected_owner_revision=(
                str(journal["expected_owner_revision"])
                if journal.get("expected_owner_revision") is not None
                else None
            ),
            mutations=mutations,
            changed=any(item.changed for item in mutations),
        )

    def _set_journal_status(
        self, journal: dict[str, Any], status: str
    ) -> dict[str, Any]:
        if status not in _JOURNAL_STATUSES:
            raise InstallationStateError("installation journal status is invalid")
        journal["status"] = status
        journal["updated_at"] = utc_now()
        self._write_journal(journal)
        return journal

    def _write_journal(self, journal: Mapping[str, Any]) -> None:
        transaction_id = str(journal.get("transaction_id") or "")
        _validate_transaction_id(transaction_id)
        _atomic_write_private_json(self.journal_path(transaction_id), journal)

    def _load_journal_if_present(self, transaction_id: str) -> dict[str, Any] | None:
        if not self.journal_path(transaction_id).exists():
            return None
        return self._load_journal(transaction_id)

    def _load_journal(self, transaction_id: str) -> dict[str, Any]:
        path = self.journal_path(transaction_id)
        payload = _read_json(path, label="installation journal")
        _validate_journal(payload, transaction_id)
        return payload

    def _assert_journal_matches_plan(
        self, journal: Mapping[str, Any], plan: InstallationPlan
    ) -> None:
        if (
            journal.get("plan_id") != plan.plan_id
            or journal.get("owner_key") != plan.owner_key
            or journal.get("manifest_sha256") != plan.manifest_sha256
        ):
            raise InstallationStateError("installation journal does not match plan")

    def _read_owner(self, owner_key: str) -> dict[str, Any] | None:
        path = self._owner_path(owner_key)
        if not path.exists():
            return None
        return self._load_owner_path(path)

    def _load_owner_path(self, path: Path) -> dict[str, Any]:
        payload = _read_json(path, label="installation ownership record")
        _validate_owner(payload, path.stem)
        return payload

    def _delete_owner_if_owned(self, owner_key: str, transaction_id: str) -> None:
        owner = self._read_owner(owner_key)
        if owner is None:
            return
        if owner.get("transaction_id") != transaction_id:
            raise InstallationConflictError(
                "installation ownership changed outside the installer"
            )
        self._owner_path(owner_key).unlink()

    def _owner_path(self, owner_key: str) -> Path:
        if not _HASH_RE.fullmatch(owner_key):
            raise InstallationStateError("installation owner key is invalid")
        return self.owners_root / f"{owner_key}.json"

    def _lock_path(self, owner_key: str) -> Path:
        if not _HASH_RE.fullmatch(owner_key):
            raise InstallationStateError("installation owner key is invalid")
        return self.locks_root / owner_key

    def _ensure_state_root(self) -> None:
        if self.data_dir.is_symlink():
            raise InstallationStateError(
                "installation data directory cannot be a symlink"
            )
        for path in (
            self.installations_root,
            self.transactions_root,
            self.owners_root,
            self.locks_root,
        ):
            if path.is_symlink():
                raise InstallationStateError("installation state cannot be a symlink")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(path, 0o700)
            except OSError:  # pragma: no cover - best-effort hardening
                pass
