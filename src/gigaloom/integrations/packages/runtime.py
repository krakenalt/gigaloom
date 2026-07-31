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
from .runtime_codec import *  # noqa: F403
from .runtime_models import *  # noqa: F403


class IntegrationRuntimeStore:
    """Capture, activate, fork, and roll back immutable integration snapshots."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "integrations" / "runtime"
        self.snapshots_root = self.root / "snapshots"
        self.sessions_root = self.root / "sessions"
        self.active_path = self.root / "active.json"
        self.bindings_path = self.root / "bindings.json"
        self.lock_path = self.root / ".runtime.lock"

    def capture(
        self,
        installer: TransactionalIntegrationInstaller,
        transaction_id: str,
    ) -> IntegrationRuntimeSnapshot:
        """Freeze one exact current committed installer transaction."""
        _validate_transaction_id(transaction_id)
        installed = installer.verify(transaction_id)
        plan = installer.transaction_plan(transaction_id)
        if plan.transaction_id != transaction_id:
            raise IntegrationRuntimeStateError("runtime transaction identity changed")
        files = _capture_files(plan)
        installed_again = installer.verify(transaction_id)
        if installed_again != installed:
            raise IntegrationRuntimeConflictError(
                "installation changed while the runtime snapshot was captured"
            )
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            active = self._read_active_unlocked()
            previous_id = active.get(plan.owner_key)
            previous = (
                self._load_id_unlocked(previous_id) if previous_id is not None else None
            )
            if previous is not None:
                _assert_same_owner(previous, plan)
                if previous.source_transaction_id == transaction_id:
                    return previous
            semantic = _snapshot_semantic(
                plan,
                owner_revision=installed.owner_revision,
                previous_snapshot_id=previous_id,
                files=files,
            )
            snapshot_hash = _json_hash(semantic)
            snapshot = IntegrationRuntimeSnapshot(
                id=f"isnap_{snapshot_hash[:32]}",
                snapshot_hash=snapshot_hash,
                package_id=plan.package_id,
                package_version=plan.package_version,
                manifest_sha256=plan.manifest_sha256,
                target_id=plan.target_id,
                scope=plan.scope,
                owner_id=plan.owner_id,
                owner_key=plan.owner_key,
                root_identity=_root_identity(plan.root),
                owner_revision=installed.owner_revision,
                source_transaction_id=transaction_id,
                previous_snapshot_id=previous_id,
                created_at=utc_now(),
                files=files,
            )
            path = self._snapshot_path(snapshot.id)
            if path.exists():
                stored = self._load_id_unlocked(snapshot.id)
                if stored != snapshot:
                    raise IntegrationRuntimeStateError(
                        "runtime snapshot id collides with different state"
                    )
            else:
                _atomic_json_write(path, _snapshot_to_record(snapshot))
            active[plan.owner_key] = snapshot.id
            self._write_active_unlocked(active)
            return snapshot

    def load(self, reference: Mapping[str, Any]) -> IntegrationRuntimeSnapshot:
        """Load and integrity-check one exact public snapshot reference."""
        snapshot_id = str(reference.get("snapshot_id") or "")
        _validate_snapshot_id(snapshot_id)
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            snapshot = self._load_id_unlocked(snapshot_id)
        expected_hash = str(reference.get("snapshot_hash") or "")
        if expected_hash != snapshot.snapshot_hash:
            raise IntegrationRuntimeStateError("runtime snapshot hash does not match")
        for field_name in (
            "package_id",
            "package_version",
            "manifest_sha256",
            "target_id",
            "source_transaction_id",
        ):
            expected = reference.get(field_name)
            if expected is not None and str(expected) != str(
                getattr(snapshot, field_name)
            ):
                raise IntegrationRuntimeStateError(
                    f"runtime snapshot {field_name} does not match"
                )
        return snapshot

    def active_for(self, reference: Mapping[str, Any]) -> IntegrationRuntimeSnapshot:
        """Return the current immutable snapshot for the referenced owner."""
        snapshot = self.load(reference)
        with exclusive_file_lock(self.lock_path):
            active_id = self._read_active_unlocked().get(snapshot.owner_key)
            if active_id is None:
                raise IntegrationRuntimeStateError(
                    "runtime owner has no active snapshot"
                )
            return self._load_id_unlocked(active_id)

    def activate_session(
        self,
        *,
        session_id: str,
        harness_id: str,
        snapshot_reference: Mapping[str, Any],
        probe: IntegrationRuntimeProbe,
        forked_from_session_id: str | None = None,
    ) -> IntegrationRuntimeBinding:
        """Materialize and prove one exact snapshot for a selected native session."""
        _validate_identity(session_id, field_name="runtime session id")
        _validate_identity(harness_id, field_name="runtime harness id")
        if forked_from_session_id is not None:
            _validate_identity(
                forked_from_session_id,
                field_name="runtime source session id",
            )
        if not callable(probe):
            raise TypeError("runtime activation probe must be callable")
        snapshot = self.load(snapshot_reference)
        if _target_harness(snapshot.target_id) != harness_id:
            raise IntegrationRuntimeConflictError(
                "runtime snapshot target does not match the selected harness"
            )
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            bindings = self._read_bindings_unlocked()
            existing = bindings.get(session_id)
            if existing is not None:
                if (
                    existing.snapshot_id == snapshot.id
                    and existing.harness_id == harness_id
                    and existing.forked_from_session_id == forked_from_session_id
                ):
                    self._assert_binding_current(existing, snapshot)
                    return existing
                raise IntegrationRuntimeConflictError(
                    "selected native session already declares another snapshot"
                )
            if forked_from_session_id is not None:
                source = bindings.get(forked_from_session_id)
                if source is None:
                    raise IntegrationRuntimeConflictError(
                        "runtime fork source session is not bound"
                    )
                source_snapshot = self._load_id_unlocked(source.snapshot_id)
                if source_snapshot.owner_key != snapshot.owner_key:
                    raise IntegrationRuntimeConflictError(
                        "runtime fork cannot change integration ownership"
                    )
            home = self._session_home(session_id)
            recovered = self._recover_binding_marker(
                home,
                session_id=session_id,
                harness_id=harness_id,
                snapshot=snapshot,
                forked_from_session_id=forked_from_session_id,
            )
            if recovered is not None:
                bindings[session_id] = recovered
                self._write_bindings_unlocked(bindings)
                return recovered
            if home.exists() or home.is_symlink():
                raise IntegrationRuntimeStateError(
                    "runtime session home exists without a valid binding"
                )
            binding = self._materialize_and_probe(
                home,
                session_id=session_id,
                harness_id=harness_id,
                snapshot=snapshot,
                forked_from_session_id=forked_from_session_id,
                probe=probe,
            )
            bindings[session_id] = binding
            self._write_bindings_unlocked(bindings)
            return binding

    def fork_session(
        self,
        *,
        source_session_id: str,
        session_id: str,
        snapshot_reference: Mapping[str, Any],
        probe: IntegrationRuntimeProbe,
    ) -> IntegrationRuntimeBinding:
        """Explicitly fork a prior session onto one reviewed snapshot."""
        _validate_identity(source_session_id, field_name="runtime source session id")
        with exclusive_file_lock(self.lock_path):
            source = self._read_bindings_unlocked().get(source_session_id)
        if source is None:
            raise IntegrationRuntimeConflictError(
                "runtime fork source session is not bound"
            )
        return self.activate_session(
            session_id=session_id,
            harness_id=source.harness_id,
            snapshot_reference=snapshot_reference,
            probe=probe,
            forked_from_session_id=source_session_id,
        )

    def binding(self, session_id: str) -> IntegrationRuntimeBinding:
        """Return one exact persisted session binding."""
        _validate_identity(session_id, field_name="runtime session id")
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            binding = self._read_bindings_unlocked().get(session_id)
            if binding is None:
                raise IntegrationRuntimeStateError(
                    "runtime session binding was not found"
                )
            snapshot = self._load_id_unlocked(binding.snapshot_id)
            self._assert_binding_current(binding, snapshot)
            return binding

    def bindings_for_integration(
        self,
        *,
        package_id: str,
        target_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Return content-free active bindings retaining one integration revision."""
        _validate_identity(package_id, field_name="runtime package id")
        _validate_identity(target_id, field_name="runtime target id")
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            bindings = self._read_bindings_unlocked()
            matched: list[dict[str, Any]] = []
            for binding in bindings.values():
                snapshot = self._load_id_unlocked(binding.snapshot_id)
                if (
                    snapshot.package_id == package_id
                    and snapshot.target_id == target_id
                ):
                    self._assert_binding_current(binding, snapshot)
                    matched.append(binding.public_projection())
        return tuple(sorted(matched, key=lambda item: str(item["session_id"])))

    def rollback(
        self,
        installer: TransactionalIntegrationInstaller,
        snapshot_reference: Mapping[str, Any],
    ) -> IntegrationRuntimeSnapshot:
        """Restore the predecessor through N4-02 and move the active pointer."""
        current = self.load(snapshot_reference)
        if current.previous_snapshot_id is None:
            raise IntegrationRuntimeConflictError(
                "runtime snapshot has no predecessor to restore"
            )
        with exclusive_file_lock(self.lock_path):
            active = self._read_active_unlocked()
            if active.get(current.owner_key) != current.id:
                raise IntegrationRuntimeConflictError(
                    "runtime snapshot is no longer active"
                )
            previous = self._load_id_unlocked(current.previous_snapshot_id)
            installer.rollback(current.source_transaction_id)
            restored = installer.verify(previous.source_transaction_id)
            plan = installer.transaction_plan(previous.source_transaction_id)
            if (
                restored.owner_revision != previous.owner_revision
                or restored.manifest_sha256 != previous.manifest_sha256
                or plan.owner_key != previous.owner_key
                or _root_identity(plan.root) != previous.root_identity
            ):
                raise IntegrationRuntimeStateError(
                    "rolled-back installation does not match the predecessor snapshot"
                )
            _assert_snapshot_files(previous, plan.root)
            active[current.owner_key] = previous.id
            self._write_active_unlocked(active)
        return previous

    def _materialize_and_probe(
        self,
        home: Path,
        *,
        session_id: str,
        harness_id: str,
        snapshot: IntegrationRuntimeSnapshot,
        forked_from_session_id: str | None,
        probe: IntegrationRuntimeProbe,
    ) -> IntegrationRuntimeBinding:
        self.sessions_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.sessions_root, 0o700)
        raw = tempfile.mkdtemp(prefix=".runtime-session-", dir=self.sessions_root)
        temporary = Path(raw)
        try:
            os.chmod(temporary, 0o700)
            _materialize_files(snapshot, temporary)
            try:
                evidence = probe(temporary, snapshot)
            except Exception as exc:
                raise IntegrationRuntimeActivationError(
                    "runtime activation probe failed; details were omitted"
                ) from exc
            if not isinstance(evidence, IntegrationRuntimeProbeResult):
                raise IntegrationRuntimeActivationError(
                    "runtime activation probe returned an invalid result"
                )
            if not evidence.discovered or not evidence.behavior_verified:
                raise IntegrationRuntimeActivationError(
                    "runtime activation did not prove discovery and behavior"
                )
            binding = IntegrationRuntimeBinding(
                session_id=session_id,
                harness_id=harness_id,
                snapshot_id=snapshot.id,
                snapshot_hash=snapshot.snapshot_hash,
                owner_key=snapshot.owner_key,
                home=str(home),
                forked_from_session_id=forked_from_session_id,
                discovery_status="verified",
                behavior_status="verified",
                probe_surface=evidence.surface,
                bound_at=utc_now(),
            )
            _atomic_json_write(
                temporary / ".gigaloom-integration-runtime.json",
                _binding_to_record(binding),
            )
            os.replace(temporary, home)
            return binding
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    def _recover_binding_marker(
        self,
        home: Path,
        *,
        session_id: str,
        harness_id: str,
        snapshot: IntegrationRuntimeSnapshot,
        forked_from_session_id: str | None,
    ) -> IntegrationRuntimeBinding | None:
        if not home.exists():
            return None
        if home.is_symlink() or not home.is_dir():
            raise IntegrationRuntimeStateError("runtime session home is unsafe")
        marker = home / ".gigaloom-integration-runtime.json"
        if marker.is_symlink() or not marker.is_file():
            return None
        binding = _binding_from_record(_read_json(marker, label="runtime marker"))
        if (
            binding.session_id != session_id
            or binding.harness_id != harness_id
            or binding.snapshot_id != snapshot.id
            or binding.snapshot_hash != snapshot.snapshot_hash
            or binding.forked_from_session_id != forked_from_session_id
        ):
            raise IntegrationRuntimeStateError("runtime session marker does not match")
        _assert_snapshot_files(snapshot, home)
        return binding

    def _assert_binding_current(
        self,
        binding: IntegrationRuntimeBinding,
        snapshot: IntegrationRuntimeSnapshot,
    ) -> None:
        home = Path(binding.home)
        expected_home = self._session_home(binding.session_id)
        if home != expected_home or home.is_symlink() or not home.is_dir():
            raise IntegrationRuntimeStateError("runtime session home is unsafe")
        marker = home / ".gigaloom-integration-runtime.json"
        persisted = _binding_from_record(_read_json(marker, label="runtime marker"))
        if persisted != binding or binding.snapshot_hash != snapshot.snapshot_hash:
            raise IntegrationRuntimeStateError("runtime session marker does not match")
        _assert_snapshot_files(snapshot, home)

    def _load_id_unlocked(self, snapshot_id: str) -> IntegrationRuntimeSnapshot:
        _validate_snapshot_id(snapshot_id)
        path = self._snapshot_path(snapshot_id)
        if path.is_symlink():
            raise IntegrationRuntimeStateError("runtime snapshot path is unsafe")
        record = _read_json(path, label="runtime snapshot")
        snapshot = _snapshot_from_record(record)
        if snapshot.id != snapshot_id:
            raise IntegrationRuntimeStateError("runtime snapshot id does not match")
        return snapshot

    def _read_active_unlocked(self) -> dict[str, str]:
        if not self.active_path.exists():
            return {}
        payload = _read_json(self.active_path, label="runtime active state")
        if payload.get("schema_version") != INTEGRATION_RUNTIME_SCHEMA_VERSION:
            raise IntegrationRuntimeStateError("runtime active schema is unsupported")
        owners = payload.get("owners")
        if not isinstance(owners, Mapping):
            raise IntegrationRuntimeStateError("runtime active state is invalid")
        parsed: dict[str, str] = {}
        for owner_key, snapshot_id in owners.items():
            _validate_hash(str(owner_key), field_name="runtime owner key")
            _validate_snapshot_id(str(snapshot_id))
            parsed[str(owner_key)] = str(snapshot_id)
        return parsed

    def _write_active_unlocked(self, active: Mapping[str, str]) -> None:
        _atomic_json_write(
            self.active_path,
            {
                "schema_version": INTEGRATION_RUNTIME_SCHEMA_VERSION,
                "owners": dict(sorted(active.items())),
            },
        )

    def _read_bindings_unlocked(self) -> dict[str, IntegrationRuntimeBinding]:
        if not self.bindings_path.exists():
            return {}
        payload = _read_json(self.bindings_path, label="runtime binding state")
        if payload.get("schema_version") != INTEGRATION_RUNTIME_SCHEMA_VERSION:
            raise IntegrationRuntimeStateError("runtime binding schema is unsupported")
        records = payload.get("bindings")
        if not isinstance(records, list):
            raise IntegrationRuntimeStateError("runtime binding state is invalid")
        bindings = tuple(_binding_from_record(item) for item in records)
        if len({item.session_id for item in bindings}) != len(bindings):
            raise IntegrationRuntimeStateError(
                "runtime binding state contains duplicate sessions"
            )
        if any(
            Path(item.home) != self._session_home(item.session_id) for item in bindings
        ):
            raise IntegrationRuntimeStateError(
                "runtime binding state contains an unsafe home"
            )
        return {item.session_id: item for item in bindings}

    def _write_bindings_unlocked(
        self, bindings: Mapping[str, IntegrationRuntimeBinding]
    ) -> None:
        _atomic_json_write(
            self.bindings_path,
            {
                "schema_version": INTEGRATION_RUNTIME_SCHEMA_VERSION,
                "bindings": [
                    _binding_to_record(item)
                    for item in sorted(
                        bindings.values(), key=lambda value: value.session_id
                    )
                ],
            },
        )

    def _snapshot_path(self, snapshot_id: str) -> Path:
        return self.snapshots_root / f"{snapshot_id}.json"

    def _session_home(self, session_id: str) -> Path:
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
        return self.sessions_root / key

    def _ensure_root(self) -> None:
        for path in (self.root, self.snapshots_root, self.sessions_root):
            if path.is_symlink():
                raise IntegrationRuntimeStateError("runtime state root is unsafe")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)


__all__ = [
    "INTEGRATION_RUNTIME_SCHEMA_VERSION",
    "IntegrationRuntimeActivationError",
    "IntegrationRuntimeBinding",
    "IntegrationRuntimeConflictError",
    "IntegrationRuntimeError",
    "IntegrationRuntimeFile",
    "IntegrationRuntimeProbe",
    "IntegrationRuntimeProbeResult",
    "IntegrationRuntimeSnapshot",
    "IntegrationRuntimeStateError",
    "IntegrationRuntimeStore",
]
