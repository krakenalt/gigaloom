"""Content-free models for bounded state-integrity diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RecoveryCheckStatus(str, Enum):
    """Stable outcome of one read-only recovery check."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class RecoveryActionKind(str, Enum):
    """Mutation class described by a preview without granting execution."""

    REBUILD_DERIVED_INDEX = "rebuild_derived_index"
    QUARANTINE_RECORD = "quarantine_record"


class RecoveryActionStatus(str, Enum):
    """Whether a preview is needed and safe to present."""

    NOT_NEEDED = "not_needed"
    RECOMMENDED = "recommended"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RecoveryScanLimits:
    """Hard limits applied before reading operator state."""

    max_files: int = 10_000
    max_records: int = 10_000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_sqlite_bytes: int = 256 * 1024 * 1024
    sqlite_timeout_seconds: float = 0.25

    def __post_init__(self) -> None:
        for field_name in (
            "max_files",
            "max_records",
            "max_file_bytes",
            "max_total_bytes",
            "max_sqlite_bytes",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")
        if not 0 < self.sqlite_timeout_seconds <= 30:
            raise ValueError("sqlite_timeout_seconds must be in (0, 30]")


@dataclass(frozen=True, slots=True)
class RecoveryCheckResult:
    """One bounded result without record content or absolute paths."""

    check_id: str
    kind: str
    source_ref: str
    status: RecoveryCheckStatus
    reason_code: str
    records_checked: int
    records_omitted: int
    evidence_digest: str
    source_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("check_id", "kind", "source_ref", "reason_code"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"{field_name} is invalid")
        if not isinstance(self.status, RecoveryCheckStatus):
            raise ValueError("status is invalid")
        for field_name in ("records_checked", "records_omitted"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        for field_name in ("evidence_digest", "source_digest"):
            value = getattr(self, field_name)
            if value is not None and (
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{field_name} must be a sha256 digest")


@dataclass(frozen=True, slots=True)
class RecoveryScanReport:
    """Deterministic read-only result for one admitted data root."""

    data_root: Path
    data_root_fingerprint: str
    check_catalog_digest: str
    checks: tuple[RecoveryCheckResult, ...]
    files_observed: int
    bytes_observed: int

    def __post_init__(self) -> None:
        if not self.data_root.is_absolute():
            raise ValueError("data_root must be absolute")
        if len(self.data_root_fingerprint) != 64:
            raise ValueError("data_root_fingerprint must be a sha256 digest")
        if len(self.check_catalog_digest) != 64:
            raise ValueError("check_catalog_digest must be a sha256 digest")
        if tuple(sorted(self.checks, key=lambda item: item.check_id)) != self.checks:
            raise ValueError("checks must be in deterministic order")
        if len({item.check_id for item in self.checks}) != len(self.checks):
            raise ValueError("check ids must be unique")
        if self.files_observed < 0 or self.bytes_observed < 0:
            raise ValueError("scan counters must be non-negative")

    @property
    def failed(self) -> bool:
        """Return whether any integrity invariant failed."""
        return any(item.status is RecoveryCheckStatus.FAILED for item in self.checks)


@dataclass(frozen=True, slots=True)
class RecoveryActionPreview:
    """Content-free description of a possible later repair action."""

    action_id: str
    kind: RecoveryActionKind
    target_ref: str
    status: RecoveryActionStatus
    reason_code: str
    source_digest: str | None
    expected_digest: str | None
    candidate_records: int
    backup_required: bool = True
    explicit_command_required: bool = True

    def __post_init__(self) -> None:
        for field_name in ("action_id", "target_ref", "reason_code"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or len(value) > 512:
                raise ValueError(f"{field_name} is invalid")
        if not isinstance(self.kind, RecoveryActionKind):
            raise ValueError("action kind is invalid")
        if not isinstance(self.status, RecoveryActionStatus):
            raise ValueError("action status is invalid")
        for field_name in ("source_digest", "expected_digest"):
            value = getattr(self, field_name)
            if value is not None and (
                len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
            ):
                raise ValueError(f"{field_name} must be a sha256 digest")
        if self.candidate_records < 0:
            raise ValueError("candidate_records must be non-negative")
        if (
            self.backup_required is not True
            or self.explicit_command_required is not True
        ):
            raise ValueError("recovery previews cannot grant mutation authority")


@dataclass(frozen=True, slots=True)
class RecoveryPreviewReport:
    """Read-only rebuild and quarantine proposals bound to one scan."""

    scan: RecoveryScanReport
    rebuilds: tuple[RecoveryActionPreview, ...]
    quarantines: tuple[RecoveryActionPreview, ...]

    def __post_init__(self) -> None:
        for field_name in ("rebuilds", "quarantines"):
            actions = getattr(self, field_name)
            if tuple(sorted(actions, key=lambda item: item.action_id)) != actions:
                raise ValueError(f"{field_name} must be in deterministic order")
        actions = (*self.rebuilds, *self.quarantines)
        if len({item.action_id for item in actions}) != len(actions):
            raise ValueError("recovery preview action ids must be unique")
