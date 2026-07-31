"""Stable state-backup schemas and content-free results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from gigaloom.runtime import store as runtime_store

RUNTIME_SCHEMA_VERSION = runtime_store.RUNTIME_SCHEMA_VERSION


BACKUP_KIND = "gigaloom_state_backup"

BACKUP_MANIFEST = "manifest.json"

BACKUP_SCHEMA_VERSION = 2

LEGACY_BACKUP_SCHEMA_VERSION = 1

STATE_LAYOUT_VERSION = 1

MINIMUM_READER_SCHEMA_VERSION = 2

_CHUNK_SIZE = 1024 * 1024

_FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

_SQLITE_SUFFIX = ".sqlite3"

_TRANSIENT_SUFFIXES = ("-shm", "-wal")

_SUPPORTED_COMPONENT_SCHEMA_VERSIONS = {
    "providers.migration": 1,
    "providers.registry": 1,
    "settings.defaults": 1,
    "settings.secret_refs": 1,
}


@dataclass(frozen=True)
class StateBackupResult:
    """Content-free result of creating or verifying one state archive."""

    schema_version: int
    harness_version: str
    file_count: int
    total_bytes: int
    sha256: str
    runtime_schema_version: int | None
    max_supported_runtime_schema_version: int
    state_layout_version: int | None
    component_schema_versions: Mapping[str, int]
    minimum_reader_schema_version: int | None
    migration_journal_sha256: str | None
    restore_compatible: bool

    def to_dict(self) -> dict[str, object]:
        """Serialize the stable result without exposing a local path."""
        return {
            "schema_version": self.schema_version,
            "harness_version": self.harness_version,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
            "runtime_schema_version": self.runtime_schema_version,
            "max_supported_runtime_schema_version": (
                self.max_supported_runtime_schema_version
            ),
            "state_layout_version": self.state_layout_version,
            "component_schema_versions": dict(self.component_schema_versions),
            "minimum_reader_schema_version": self.minimum_reader_schema_version,
            "migration_journal_sha256": self.migration_journal_sha256,
            "restore_compatible": self.restore_compatible,
        }


@dataclass(frozen=True)
class StateRestoreResult:
    """Content-free result of atomically restoring one state archive."""

    backup: StateBackupResult
    replaced_existing: bool

    def to_dict(self) -> dict[str, object]:
        """Serialize restore evidence without exposing local paths."""
        return {
            **self.backup.to_dict(),
            "restored": True,
            "replaced_existing": self.replaced_existing,
        }
