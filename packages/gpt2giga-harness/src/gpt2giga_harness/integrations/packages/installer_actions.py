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


class _InstallerActionsMixin:
    """Implementation slice for the transactional installer."""

    def apply(
        self,
        request: InstallationRequest,
        plan: InstallationPlan,
        approval: InstallationApproval,
        *,
        verifier: InstallationVerifier,
    ) -> InstallationResult:
        """Apply one exact approved plan, or return its prior committed result."""
        if not callable(verifier):
            raise TypeError("installation verifier must be callable")
        self._validate_plan_request(request, plan)
        if approval.plan_id != plan.plan_id:
            raise InstallationConflictError(
                "installation approval does not match the current preview"
            )
        if plan.scope is InstallationScope.USER_HOME and not approval.allow_user_home:
            raise InstallationScopeError(
                "user-home installation requires explicit approval"
            )
        self._ensure_state_root()
        with exclusive_file_lock(self._lock_path(plan.owner_key)):
            repeated = self._repeated_result(plan)
            if repeated is not None:
                return repeated
            current_owner = self._read_owner(plan.owner_key)
            current_revision = str(current_owner["revision"]) if current_owner else None
            if current_revision != plan.expected_owner_revision:
                raise InstallationConflictError(
                    "installation ownership changed after preview"
                )
            if current_owner is not None:
                installed = self._installed_from_owner(current_owner)
                if installed.current and self._owner_matches_plan(current_owner, plan):
                    return self._result_from_owner(current_owner)
                raise InstallationConflictError(
                    "existing installation requires an explicit update transaction"
                )
            if self.target_active(plan.root):
                raise InstallationConflictError(
                    "installation target is active; stop its native process first"
                )
            self._assert_preview_current(plan)
            return self._commit(
                request,
                plan,
                approval,
                verifier=verifier,
                previous_owner=None,
            )

    def update(
        self,
        request: InstallationRequest,
        plan: InstallationPlan,
        approval: InstallationApproval,
        *,
        verifier: InstallationVerifier,
    ) -> InstallationResult:
        """Atomically replace one exact current owner and retain rollback history."""
        if not callable(verifier):
            raise TypeError("installation verifier must be callable")
        self._validate_plan_request(request, plan)
        if approval.plan_id != plan.plan_id:
            raise InstallationConflictError(
                "installation approval does not match the current preview"
            )
        if plan.scope is InstallationScope.USER_HOME and not approval.allow_user_home:
            raise InstallationScopeError(
                "user-home installation requires explicit approval"
            )
        self._ensure_state_root()
        with exclusive_file_lock(self._lock_path(plan.owner_key)):
            repeated = self._repeated_result(plan)
            if repeated is not None:
                return repeated
            current_owner = self._read_owner(plan.owner_key)
            if current_owner is None:
                raise InstallationConflictError(
                    "installation update requires an existing owner"
                )
            if current_owner["revision"] != plan.expected_owner_revision:
                raise InstallationConflictError(
                    "installation ownership changed after preview"
                )
            installed = self._installed_from_owner(current_owner)
            if not installed.current:
                raise InstallationConflictError(
                    "installed target changed outside the installer"
                )
            owned_paths = tuple(
                str(item["relative_path"]) for item in current_owner["files"]
            )
            planned_paths = tuple(item.relative_path for item in plan.mutations)
            if planned_paths != owned_paths:
                raise InstallationConflictError(
                    "installation update must replace the exact owned file set"
                )
            if self.target_active(plan.root):
                raise InstallationConflictError(
                    "installation target is active; stop its native process first"
                )
            self._assert_preview_current(plan)
            return self._commit(
                request,
                plan,
                approval,
                verifier=verifier,
                previous_owner=current_owner,
            )

    def discover(self) -> tuple[InstalledIntegration, ...]:
        """Discover all private ownership records and report exact hash drift."""
        self._ensure_state_root()
        owners = [
            self._load_owner_path(path)
            for path in sorted(self.owners_root.glob("*.json"))
        ]
        return tuple(self._installed_from_owner(owner) for owner in owners)

    def verify(self, transaction_id: str) -> InstalledIntegration:
        """Verify that one committed transaction still owns its exact files."""
        journal = self._load_journal(transaction_id)
        if journal["status"] != "committed":
            raise InstallationStateError("installation transaction is not committed")
        owner = self._read_owner(str(journal["owner_key"]))
        if owner is None or owner.get("transaction_id") != transaction_id:
            raise InstallationStateError("installation ownership record is missing")
        return self._installed_from_owner(owner)

    def rollback(self, transaction_id: str) -> InstallationResult:
        """Restore one committed transaction after exact ownership checks."""
        journal = self._load_journal(transaction_id)
        if journal["status"] == "rolled_back":
            return self._result_from_journal(journal)
        if journal["status"] != "committed":
            raise InstallationStateError("installation transaction cannot roll back")
        root = self._validate_journal_scope(journal)
        with exclusive_file_lock(self._lock_path(str(journal["owner_key"]))):
            if self.target_active(root):
                raise InstallationConflictError(
                    "installation target is active; stop its native process first"
                )
            owner = self._read_owner(str(journal["owner_key"]))
            if owner is None or owner.get("transaction_id") != transaction_id:
                raise InstallationConflictError(
                    "installation ownership changed outside the installer"
                )
            if not self._installed_from_owner(owner).current:
                raise InstallationConflictError(
                    "installed target changed outside the installer"
                )
            journal["failure"] = None
            return self._result_from_journal(self._rollback_from_journal(journal))

    def recover(
        self,
        verifiers: Mapping[str, InstallationVerifier] | None = None,
    ) -> tuple[InstallationRecoveryResult, ...]:
        """Reconcile every interrupted transaction without guessing authority."""
        self._ensure_state_root()
        verifier_map = dict(verifiers or {})
        journals = []
        for transaction_dir in sorted(self.transactions_root.glob("txn_*")):
            if transaction_dir.is_symlink() or not transaction_dir.is_dir():
                raise InstallationStateError("installation transaction path is unsafe")
            journal = self._load_journal(transaction_dir.name)
            if journal["status"] not in {"committed", "rolled_back"}:
                self._validate_journal_scope(journal)
                journals.append(journal)
        outcomes: list[InstallationRecoveryResult] = []
        for journal in journals:
            owner_key = str(journal["owner_key"])
            with exclusive_file_lock(self._lock_path(owner_key)):
                if journal["status"] == "rolling_back":
                    self._rollback_from_journal(journal)
                    outcome = "rolled_back"
                else:
                    verifier = verifier_map.get(str(journal["target_id"]))
                    if verifier is None:
                        self._assert_recoverable_files(journal)
                        self._rollback_from_journal(journal)
                        outcome = "restored"
                    else:
                        self._assert_recoverable_files(journal)
                        try:
                            journal = self._apply_staged(journal)
                            journal = self._set_journal_status(journal, "verifying")
                            plan = self._plan_from_journal(journal)
                            if not verifier(plan.root, plan):
                                raise InstallationVerificationError(
                                    "installation verification failed during recovery"
                                )
                            owner = self._publish_owner(journal)
                            journal["owner_revision"] = owner["revision"]
                            self._set_journal_status(journal, "committed")
                            outcome = "completed"
                        except Exception as exc:
                            journal["failure"] = _bounded_failure(exc)
                            self._write_journal(journal)
                            self._rollback_from_journal(journal)
                            outcome = "restored"
                outcomes.append(
                    InstallationRecoveryResult(
                        transaction_id=str(journal["transaction_id"]),
                        outcome=outcome,
                    )
                )
        return tuple(outcomes)

    def journal_path(self, transaction_id: str) -> Path:
        """Return the validated private journal path for diagnostics/tests."""
        _validate_transaction_id(transaction_id)
        return self.transactions_root / transaction_id / "journal.json"

    def transaction_plan(self, transaction_id: str) -> InstallationPlan:
        """Return the content-free plan bound to one validated transaction."""
        return self._plan_from_journal(self._load_journal(transaction_id))

    def _repeated_result(self, plan: InstallationPlan) -> InstallationResult | None:
        existing_journal = self._load_journal_if_present(plan.transaction_id)
        if existing_journal is None:
            return None
        self._assert_journal_matches_plan(existing_journal, plan)
        if existing_journal["status"] != "committed":
            raise InstallationConflictError(
                "installation transaction is no longer applicable"
            )
        installed = self._installed_from_journal(existing_journal)
        if not installed.current:
            raise InstallationConflictError(
                "installed target changed outside the installer"
            )
        return self._result_from_journal(existing_journal)

    def _commit(
        self,
        request: InstallationRequest,
        plan: InstallationPlan,
        approval: InstallationApproval,
        *,
        verifier: InstallationVerifier,
        previous_owner: Mapping[str, Any] | None,
    ) -> InstallationResult:
        journal = self._prepare_transaction(
            request,
            plan,
            approval,
            previous_owner=previous_owner,
        )
        try:
            journal = self._apply_staged(journal)
            journal = self._set_journal_status(journal, "verifying")
            if not verifier(plan.root, plan):
                raise InstallationVerificationError("installation verification failed")
            owner = self._publish_owner(journal)
            journal["owner_revision"] = owner["revision"]
            journal = self._set_journal_status(journal, "committed")
        except Exception as exc:
            journal["failure"] = _bounded_failure(exc)
            self._write_journal(journal)
            self._rollback_from_journal(journal)
            raise
        return self._result_from_journal(journal)
