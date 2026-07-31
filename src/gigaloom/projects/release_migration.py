"""Backup-gated 0.6 to 0.7 Native Agent Gateway state upgrade."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .backup import create_state_backup, verify_state_backup
from .catalog.migration import (
    PROJECT_CATALOG_MIGRATION_ID,
    ProjectCatalogMigrationService,
)
from .catalog.migration_io import (
    canonical_migration_json,
    read_migration_json,
    write_migration_json,
)
from .catalog.repository import (
    FilesystemProjectCatalogRepository,
    _exclusive_file_lock,
)


NATIVE_AGENT_GATEWAY_MIGRATION_ID = "native_agent_gateway_v1"
NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION = 1
NATIVE_AGENT_GATEWAY_TARGET_VERSION = "0.7.0"
_ACCEPTED_NATIVE_AGENT_GATEWAY_TARGET_VERSIONS = frozenset(
    {"0.7.0a1", NATIVE_AGENT_GATEWAY_TARGET_VERSION}
)
TEXTUAL_PREFERENCES_RETIREMENT_ID = "textual_preferences_retirement_v1"
_LEGACY_WORKBENCH_PREFERENCES = Path("settings") / "workbench.json"
_LEGACY_WORKBENCH_LOCK = Path("settings") / "workbench.lock"
_PHASES = (
    "planned",
    "backed_up",
    "project_catalog_completed",
    "textual_preferences_planned",
    "textual_preferences_retired",
    "completed",
)


class InjectedNativeAgentGatewayMigrationCrash(RuntimeError):
    """Test-only interruption after one durable release-migration boundary."""


class MigrationSessionStore(Protocol):
    """Session store surface required by the Project Catalog migration."""

    def list_sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> tuple[Any, ...]: ...

    def get_session(self, session_id: str) -> Any: ...

    def update_session_if_revision(
        self,
        session_id: str,
        expected_updated_at: str,
        **patch: Any,
    ) -> Any | None: ...


@dataclass(frozen=True)
class NativeAgentGatewayMigrationReceiptV1:
    """Content-free proof of the ordered, backup-gated 0.7 upgrade."""

    migration_id: str
    status: str
    source_version: str
    target_version: str
    backup_sha256: str
    ordered_steps: tuple[str, ...]
    project_catalog_receipt_digest: str
    textual_preferences_present: bool
    textual_preferences_digest: str | None
    omissions: tuple[str, ...]
    completed_at: str
    receipt_digest: str
    schema_version: int = NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        """Serialize content-free migration evidence."""
        return {
            "schema_version": self.schema_version,
            "migration_id": self.migration_id,
            "status": self.status,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "backup_sha256": self.backup_sha256,
            "ordered_steps": list(self.ordered_steps),
            "project_catalog_receipt_digest": (self.project_catalog_receipt_digest),
            "textual_preferences_present": self.textual_preferences_present,
            "textual_preferences_digest": self.textual_preferences_digest,
            "omissions": list(self.omissions),
            "completed_at": self.completed_at,
            "receipt_digest": self.receipt_digest,
        }


class NativeAgentGatewayMigrationService:
    """Upgrade one quiescent 0.6 state tree through resumable ordered steps."""

    def __init__(
        self,
        data_dir: str | Path,
        backup_path: str | Path,
        *,
        session_store: MigrationSessionStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.backup_path = Path(backup_path).expanduser().resolve()
        if not self.data_dir.is_dir():
            raise ValueError("0.6 state directory does not exist")
        if self.backup_path == self.data_dir or _is_relative_to(
            self.backup_path, self.data_dir
        ):
            raise ValueError("0.7 upgrade backup must be outside active state")
        self.support_dir = self.backup_path.parent / (
            f".{self.backup_path.name}.{NATIVE_AGENT_GATEWAY_MIGRATION_ID}"
        )
        if _is_relative_to(self.support_dir, self.data_dir):
            raise ValueError(
                "0.7 upgrade support directory must be outside active state"
            )
        self.journal_path = self.support_dir / "journal.json"
        self.lock_path = self.support_dir / "migration"
        self.receipt_path = (
            self.data_dir
            / "migrations"
            / f"{NATIVE_AGENT_GATEWAY_MIGRATION_ID}.receipt.json"
        )
        self._session_store = session_store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def migrate(
        self,
        *,
        crash_after_phase: str | None = None,
        catalog_crash_after_step: int | None = None,
    ) -> NativeAgentGatewayMigrationReceiptV1:
        """Apply or resume the complete release migration after an exact backup."""
        if crash_after_phase is not None and crash_after_phase not in _PHASES:
            raise ValueError("unknown injected release migration phase")
        with _exclusive_file_lock(self.lock_path):
            journal = (
                read_migration_json(self.journal_path)
                if self.journal_path.exists()
                else self._new_journal()
            )
            _validate_journal(journal, data_dir=self.data_dir, backup=self.backup_path)
            phase = str(journal["phase"])
            self._inject(crash_after_phase, phase)
            if phase == "completed":
                return _receipt_from_dict(read_migration_json(self.receipt_path))

            if phase == "planned":
                backup = self._ensure_exact_backup()
                journal.update(
                    phase="backed_up",
                    backup_sha256=backup.sha256,
                    backup_file_count=backup.file_count,
                    backup_total_bytes=backup.total_bytes,
                )
                self._write_journal(journal)
                phase = "backed_up"
                self._inject(crash_after_phase, phase)
            else:
                self._verify_recorded_backup(journal)

            if phase == "backed_up":
                catalog_receipt = self._catalog_migration().migrate(
                    crash_after_step=catalog_crash_after_step
                )
                journal.update(
                    phase="project_catalog_completed",
                    project_catalog_receipt_digest=catalog_receipt.receipt_digest,
                )
                self._write_journal(journal)
                phase = "project_catalog_completed"
                self._inject(crash_after_phase, phase)

            if phase == "project_catalog_completed":
                preferences = self.data_dir / _LEGACY_WORKBENCH_PREFERENCES
                present, digest = _legacy_preferences_evidence(preferences)
                journal.update(
                    phase="textual_preferences_planned",
                    textual_preferences_present=present,
                    textual_preferences_digest=digest,
                )
                self._write_journal(journal)
                phase = "textual_preferences_planned"
                self._inject(crash_after_phase, phase)

            if phase == "textual_preferences_planned":
                self._retire_textual_preferences(journal)
                journal["phase"] = "textual_preferences_retired"
                self._write_journal(journal)
                phase = "textual_preferences_retired"
                self._inject(crash_after_phase, phase)

            if phase == "textual_preferences_retired":
                receipt = self._complete_receipt(journal)
                write_migration_json(self.receipt_path, receipt.to_dict())
                journal.update(
                    phase="completed",
                    completed_at=receipt.completed_at,
                    receipt_digest=receipt.receipt_digest,
                )
                self._write_journal(journal)
                self._inject(crash_after_phase, "completed")
                return receipt
            raise ValueError("unsupported 0.7 release migration phase")

    def _new_journal(self) -> dict[str, object]:
        journal: dict[str, object] = {
            "schema_version": NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION,
            "migration_id": NATIVE_AGENT_GATEWAY_MIGRATION_ID,
            "phase": "planned",
            "source_version": "0.6.0a1",
            "target_version": NATIVE_AGENT_GATEWAY_TARGET_VERSION,
            "data_dir": str(self.data_dir),
            "backup_path": str(self.backup_path),
            "created_at": _timestamp(self._clock()),
        }
        self._write_journal(journal)
        return journal

    def _write_journal(self, journal: dict[str, object]) -> None:
        write_migration_json(self.journal_path, journal)

    def _ensure_exact_backup(self):
        if not self.backup_path.exists():
            return create_state_backup(self.data_dir, self.backup_path)
        existing = verify_state_backup(self.backup_path)
        comparison = self.support_dir / f"comparison-{uuid4().hex}.zip"
        try:
            current = create_state_backup(self.data_dir, comparison)
            if current.sha256 != existing.sha256:
                raise ValueError(
                    "active state differs from the interrupted upgrade backup"
                )
        finally:
            comparison.unlink(missing_ok=True)
        return existing

    def _verify_recorded_backup(self, journal: dict[str, object]) -> None:
        verified = verify_state_backup(self.backup_path)
        if verified.sha256 != journal.get("backup_sha256"):
            raise ValueError("0.7 upgrade backup changed after verification")

    def _catalog_migration(self) -> ProjectCatalogMigrationService:
        session_store = self._session_store
        if session_store is None:
            from gigaloom.sessions import FilesystemHarnessSessionStore

            session_store = FilesystemHarnessSessionStore(self.data_dir)
            self._session_store = session_store
        return ProjectCatalogMigrationService(
            FilesystemProjectCatalogRepository(self.data_dir / "projects" / "catalog"),
            session_store,
            active_state_dir=self.data_dir,
            backup_root=self.support_dir / "project-catalog-backups",
            clock=self._clock,
        )

    def _retire_textual_preferences(self, journal: dict[str, object]) -> None:
        preferences = self.data_dir / _LEGACY_WORKBENCH_PREFERENCES
        expected_present = journal.get("textual_preferences_present") is True
        expected_digest = journal.get("textual_preferences_digest")
        if preferences.exists() or preferences.is_symlink():
            present, digest = _legacy_preferences_evidence(preferences)
            if not expected_present or digest != expected_digest:
                raise ValueError("legacy Textual preferences changed during upgrade")
            preferences.unlink()
            _fsync_directory(preferences.parent)
        elif not expected_present:
            pass
        legacy_lock = self.data_dir / _LEGACY_WORKBENCH_LOCK
        if legacy_lock.is_symlink() or (
            legacy_lock.exists() and not legacy_lock.is_file()
        ):
            raise ValueError("legacy Textual preference lock is unsafe")
        legacy_lock.unlink(missing_ok=True)
        if legacy_lock.parent.exists():
            _fsync_directory(legacy_lock.parent)

    def _complete_receipt(
        self, journal: dict[str, object]
    ) -> NativeAgentGatewayMigrationReceiptV1:
        payload: dict[str, object] = {
            "schema_version": NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION,
            "migration_id": NATIVE_AGENT_GATEWAY_MIGRATION_ID,
            "status": "completed",
            "source_version": "0.6.0a1",
            "target_version": journal["target_version"],
            "backup_sha256": journal["backup_sha256"],
            "ordered_steps": [
                PROJECT_CATALOG_MIGRATION_ID,
                TEXTUAL_PREFERENCES_RETIREMENT_ID,
            ],
            "project_catalog_receipt_digest": journal["project_catalog_receipt_digest"],
            "textual_preferences_present": journal["textual_preferences_present"],
            "textual_preferences_digest": journal["textual_preferences_digest"],
            "omissions": [
                "session_content",
                "repository_content",
                "preference_values",
                "credentials",
            ],
            "completed_at": _timestamp(self._clock()),
            "receipt_digest": "",
        }
        payload["receipt_digest"] = sha256(
            canonical_migration_json(payload)
        ).hexdigest()
        return _receipt_from_dict(payload)

    @staticmethod
    def _inject(requested: str | None, phase: str) -> None:
        if requested == phase:
            raise InjectedNativeAgentGatewayMigrationCrash(
                f"injected Native Agent Gateway migration crash after {phase}"
            )


def _legacy_preferences_evidence(path: Path) -> tuple[bool, str | None]:
    if not path.exists() and not path.is_symlink():
        return False, None
    if path.is_symlink() or not path.is_file():
        raise ValueError("legacy Textual preferences must be a regular file")
    return True, sha256(path.read_bytes()).hexdigest()


def _validate_journal(
    journal: dict[str, object], *, data_dir: Path, backup: Path
) -> None:
    if (
        journal.get("schema_version") != NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION
        or journal.get("migration_id") != NATIVE_AGENT_GATEWAY_MIGRATION_ID
        or journal.get("phase") not in _PHASES
        or journal.get("source_version") != "0.6.0a1"
        or journal.get("target_version")
        not in _ACCEPTED_NATIVE_AGENT_GATEWAY_TARGET_VERSIONS
        or journal.get("data_dir") != str(data_dir)
        or journal.get("backup_path") != str(backup)
    ):
        raise ValueError("0.7 release migration journal fields are invalid")


def _receipt_from_dict(
    data: dict[str, Any],
) -> NativeAgentGatewayMigrationReceiptV1:
    if (
        data.get("schema_version") != NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION
        or data.get("migration_id") != NATIVE_AGENT_GATEWAY_MIGRATION_ID
        or data.get("status") != "completed"
    ):
        raise ValueError("0.7 release migration receipt fields are invalid")
    payload = dict(data)
    digest = payload.get("receipt_digest")
    payload["receipt_digest"] = ""
    if (
        not isinstance(digest, str)
        or sha256(canonical_migration_json(payload)).hexdigest() != digest
    ):
        raise ValueError("0.7 release migration receipt digest mismatch")
    return NativeAgentGatewayMigrationReceiptV1(
        migration_id=str(data["migration_id"]),
        status=str(data["status"]),
        source_version=str(data["source_version"]),
        target_version=str(data["target_version"]),
        backup_sha256=str(data["backup_sha256"]),
        ordered_steps=tuple(str(item) for item in data["ordered_steps"]),
        project_catalog_receipt_digest=str(data["project_catalog_receipt_digest"]),
        textual_preferences_present=bool(data["textual_preferences_present"]),
        textual_preferences_digest=(
            None
            if data["textual_preferences_digest"] is None
            else str(data["textual_preferences_digest"])
        ),
        omissions=tuple(str(item) for item in data["omissions"]),
        completed_at=str(data["completed_at"]),
        receipt_digest=digest,
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("release migration clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "InjectedNativeAgentGatewayMigrationCrash",
    "NATIVE_AGENT_GATEWAY_MIGRATION_ID",
    "NATIVE_AGENT_GATEWAY_MIGRATION_SCHEMA_VERSION",
    "NativeAgentGatewayMigrationReceiptV1",
    "NativeAgentGatewayMigrationService",
    "TEXTUAL_PREFERENCES_RETIREMENT_ID",
]
