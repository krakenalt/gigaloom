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
from .installer_journal import *  # noqa: F403
from .installer_models import *  # noqa: F403
from .installer_primitives import *  # noqa: F403
from .installer_support import *  # noqa: F403


class _InstallerPlanningMixin:
    """Implementation slice for the transactional installer."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        project_roots: Sequence[str | Path] = (),
        user_home_root: str | Path | None = None,
        allow_user_home: bool = False,
        target_active: Callable[[Path], bool] | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self.data_dir = _absolute_path(Path(data_dir))
        self.installations_root = self.data_dir / "integrations" / "installations"
        self.transactions_root = self.installations_root / "transactions"
        self.owners_root = self.installations_root / "owners"
        self.locks_root = self.installations_root / "locks"
        self.project_roots = tuple(
            sorted({_absolute_path(Path(item)) for item in project_roots}, key=str)
        )
        self.user_home_root = (
            _absolute_path(Path(user_home_root)) if user_home_root is not None else None
        )
        self.allow_user_home = allow_user_home
        self.target_active = target_active or (lambda _root: False)
        self.fault_injector = fault_injector or (lambda _phase, _transaction_id: None)

    def preview(self, request: InstallationRequest) -> InstallationPlan:
        """Build a deterministic content-free plan without changing the target."""
        root = self._validate_request_scope(request)
        self._ensure_state_root()
        owner_key = _owner_key(request.target, root)
        owner = self._read_owner(owner_key)
        mutations = tuple(
            self._preview_mutation(root, mutation) for mutation in request.mutations
        )
        manifest_hash = integration_package_semantic_hash(request.package)
        semantic = _plan_semantic(
            package_id=request.package.id,
            package_version=request.package.version,
            manifest_sha256=manifest_hash,
            target_id=request.target.id,
            scope=request.target.scope,
            owner_id=request.target.owner_id,
            owner_key=owner_key,
            root=root,
            expected_owner_revision=(str(owner["revision"]) if owner else None),
            mutations=mutations,
        )
        plan_hash = _json_hash(semantic)
        return InstallationPlan(
            plan_id=f"plan_{plan_hash}",
            transaction_id=f"txn_{plan_hash[:32]}",
            package_id=request.package.id,
            package_version=request.package.version,
            manifest_sha256=manifest_hash,
            target_id=request.target.id,
            scope=request.target.scope,
            owner_id=request.target.owner_id,
            owner_key=owner_key,
            root=root,
            expected_owner_revision=(str(owner["revision"]) if owner else None),
            mutations=mutations,
            changed=any(item.changed for item in mutations),
        )

    def _validate_request_scope(self, request: InstallationRequest) -> Path:
        if request.target.scope not in request.package.scopes:
            raise InstallationScopeError("package does not support requested scope")
        if not any(
            item.target_id == request.target.id
            for item in request.package.compatibility
        ):
            raise InstallationScopeError("package does not support requested target")
        if assess_integration_package(request.package).decision in {
            IntegrationTrustDecision.BLOCKED,
            IntegrationTrustDecision.PROVIDER_HANDOFF,
        }:
            raise InstallationScopeError(
                "package trust policy does not permit local installation"
            )
        return self._validate_scope_root(request.target.scope, request.target.root)

    def _validate_scope_root(self, scope: InstallationScope, raw_root: Path) -> Path:
        root = _absolute_path(raw_root)
        if scope is InstallationScope.MANAGED_HOME:
            admitted = self.data_dir / "native"
            if root == admitted or not _is_relative_to(root, admitted):
                raise InstallationScopeError(
                    "managed-home target must be inside Harness native state"
                )
            _assert_no_symlink_chain(root, admitted)
            return root
        if scope is InstallationScope.PROJECT:
            admitted = next(
                (
                    candidate
                    for candidate in self.project_roots
                    if root == candidate or _is_relative_to(root, candidate)
                ),
                None,
            )
            if admitted is None:
                raise InstallationScopeError(
                    "project target must be inside an explicitly admitted project root"
                )
            _assert_no_symlink_chain(root, admitted)
            return root
        if not self.allow_user_home or self.user_home_root is None:
            raise InstallationScopeError(
                "user-home installation is disabled by default"
            )
        if root != self.user_home_root:
            raise InstallationScopeError(
                "user-home target must match the explicitly configured root"
            )
        _assert_no_symlink_chain(root, root)
        return root

    def _validate_journal_scope(self, journal: Mapping[str, Any]) -> Path:
        try:
            scope = InstallationScope(str(journal["scope"]))
            root = Path(str(journal["root"]))
        except (KeyError, ValueError) as exc:
            raise InstallationStateError(
                "installation journal scope is invalid"
            ) from exc
        return self._validate_scope_root(scope, root)

    def _validate_plan_request(
        self, request: InstallationRequest, plan: InstallationPlan
    ) -> None:
        root = self._validate_request_scope(request)
        if root != plan.root:
            raise InstallationConflictError(
                "installation plan root does not match request"
            )
        manifest_hash = integration_package_semantic_hash(request.package)
        desired = {
            item.relative_path: (_bytes_hash(item.content), item.mode)
            for item in request.mutations
        }
        planned = {
            item.relative_path: (item.desired_sha256, item.mode)
            for item in plan.mutations
        }
        expected = (
            request.package.id,
            request.package.version,
            manifest_hash,
            request.target.id,
            request.target.scope,
            request.target.owner_id,
            _owner_key(request.target, root),
            desired,
        )
        actual = (
            plan.package_id,
            plan.package_version,
            plan.manifest_sha256,
            plan.target_id,
            plan.scope,
            plan.owner_id,
            plan.owner_key,
            planned,
        )
        if actual != expected:
            raise InstallationConflictError("installation plan does not match request")

    def _preview_mutation(
        self, root: Path, mutation: FileInstallMutation
    ) -> InstallationFilePlan:
        path = _target_path(root, mutation.relative_path)
        current = _read_regular_file(path)
        current_hash = _bytes_hash(current) if current is not None else None
        current_mode = stat_mode(path) if current is not None else None
        desired_hash = _bytes_hash(mutation.content)
        return InstallationFilePlan(
            relative_path=mutation.relative_path,
            current_sha256=current_hash,
            current_mode=current_mode,
            desired_sha256=desired_hash,
            mode=mutation.mode,
            changed=current_hash != desired_hash or current_mode != mutation.mode,
        )

    def _assert_preview_current(self, plan: InstallationPlan) -> None:
        for mutation in plan.mutations:
            path = _target_path(plan.root, mutation.relative_path)
            current = _read_regular_file(path)
            current_hash = _bytes_hash(current) if current is not None else None
            current_mode = stat_mode(path) if current is not None else None
            if (
                current_hash != mutation.current_sha256
                or current_mode != mutation.current_mode
            ):
                raise InstallationConflictError(
                    "installation target changed after preview; refresh the plan"
                )

    def _prepare_transaction(
        self,
        request: InstallationRequest,
        plan: InstallationPlan,
        approval: InstallationApproval,
        *,
        previous_owner: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        transaction_dir = self.transactions_root / plan.transaction_id
        if transaction_dir.exists() or transaction_dir.is_symlink():
            raise InstallationStateError("installation transaction path already exists")
        transaction_dir.mkdir(mode=0o700)
        os.chmod(transaction_dir, 0o700)
        stage_dir = transaction_dir / "stage"
        backup_dir = transaction_dir / "backups"
        stage_dir.mkdir(mode=0o700)
        backup_dir.mkdir(mode=0o700)
        records = []
        by_path = {item.relative_path: item for item in request.mutations}
        for index, mutation_plan in enumerate(plan.mutations):
            mutation = by_path[mutation_plan.relative_path]
            stage_name = f"{index:04d}.payload"
            backup_name = f"{index:04d}.backup"
            _atomic_write_private_bytes(stage_dir / stage_name, mutation.content)
            target = _target_path(plan.root, mutation.relative_path)
            current = _read_regular_file(target)
            existed = current is not None
            if current is not None:
                _atomic_write_private_bytes(backup_dir / backup_name, current)
            records.append(
                {
                    **_file_plan_to_dict(mutation_plan),
                    "stage_file": stage_name,
                    "backup_file": backup_name if existed else None,
                    "existed": existed,
                }
            )
        now = utc_now()
        journal = {
            "schema_version": INSTALLATION_JOURNAL_SCHEMA_VERSION,
            "transaction_id": plan.transaction_id,
            "plan_id": plan.plan_id,
            "status": "prepared",
            "package_id": plan.package_id,
            "package_version": plan.package_version,
            "manifest_sha256": plan.manifest_sha256,
            "target_id": plan.target_id,
            "scope": plan.scope.value,
            "owner_id": plan.owner_id,
            "owner_key": plan.owner_key,
            "root": str(plan.root),
            "expected_owner_revision": plan.expected_owner_revision,
            "approval_authority": approval.authority,
            "allow_user_home": approval.allow_user_home,
            "mutations": records,
            "applied_count": 0,
            "rollback_count": 0,
            "owner_revision": None,
            "operation": "update" if previous_owner is not None else "install",
            "previous_owner": dict(previous_owner)
            if previous_owner is not None
            else None,
            "failure": None,
            "created_at": now,
            "updated_at": now,
        }
        self._write_journal(journal)
        return journal
