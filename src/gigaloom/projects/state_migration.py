"""Backup-first cutover from the legacy Harness state root."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import threading
from typing import Literal
from uuid import uuid4

from .backup_io import _publish_restored_state
from .state_migration_io import (
    TreeEvidence,
    copy_verified_tree,
    snapshot_tree,
    verify_tree,
)


CANONICAL_STATE_RELATIVE_PATH = Path(".gigaloom")
LEGACY_STATE_RELATIVE_PATH = Path(".gpt2giga") / "harness"
MIGRATION_SUPPORT_RELATIVE_PATH = Path(".gigaloom-migration")
STATE_MIGRATION_SCHEMA_VERSION = 1
STATE_MIGRATION_ID = "gigaloom-state-root-v1"
_BACKUP_RELATIVE_PATH = Path("backups") / "legacy-state"
_JOURNAL_RELATIVE_PATH = Path("journal.json")
_STAGE_RELATIVE_PATH = Path(".gigaloom.migration-stage")
_PHASES = {"planned", "backed_up", "staged", "promoted", "complete", "rolled_back"}
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


class InjectedStateMigrationCrash(RuntimeError):
    """Raised by hermetic tests after one durable migration phase."""


@dataclass(frozen=True)
class StateMigrationResult:
    """Content-free state-root migration evidence."""

    status: Literal["current", "migrated", "rolled_back"]
    applied: bool
    source_root: str
    target_root: str
    backup_root: str | None
    backup_sha256: str | None
    file_count: int
    total_bytes: int
    runtime_schema_version: int | None

    def to_dict(self) -> dict[str, object]:
        """Serialize evidence without exposing contents or absolute paths."""
        return {
            "schema_version": STATE_MIGRATION_SCHEMA_VERSION,
            "migration_id": STATE_MIGRATION_ID,
            "status": self.status,
            "applied": self.applied,
            "source_root": self.source_root,
            "target_root": self.target_root,
            "backup_root": self.backup_root,
            "backup_sha256": self.backup_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "runtime_schema_version": self.runtime_schema_version,
        }


def prepare_runtime_state(
    *,
    home: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> StateMigrationResult:
    """Prepare the default canonical root before normal runtime startup."""
    environment = os.environ if environ is None else environ
    reject_legacy_state_override(environment)
    configured = environment.get("GIGALOOM_DATA_DIR", "").strip()
    if configured:
        return StateMigrationResult(
            status="current",
            applied=False,
            source_root=LEGACY_STATE_RELATIVE_PATH.as_posix(),
            target_root="GIGALOOM_DATA_DIR",
            backup_root=None,
            backup_sha256=None,
            file_count=0,
            total_bytes=0,
            runtime_schema_version=None,
        )
    return migrate_legacy_state(home=home)


def reject_legacy_state_override(environ: Mapping[str, str] | None = None) -> None:
    """Reject the removed data-root override with explicit remediation."""
    environment = os.environ if environ is None else environ
    if environment.get("GPT2GIGA_HARNESS_DATA_DIR", "").strip():
        raise ValueError(
            "GPT2GIGA_HARNESS_DATA_DIR is no longer supported. Unset it and use "
            "GIGALOOM_DATA_DIR for a custom canonical root, or run "
            "'giga state migrate' for the default one-way cutover."
        )


def migrate_legacy_state(
    *,
    home: str | Path | None = None,
    _crash_after: str | None = None,
) -> StateMigrationResult:
    """Back up, stage, verify, and atomically promote legacy state."""
    root = _resolved_home(home)
    legacy, canonical, support, journal_path, backup, stage = _paths(root)
    journal_exists = journal_path.is_file()

    if not journal_exists:
        if canonical.exists() and legacy.exists():
            raise _root_collision_error()
        if canonical.exists() or not legacy.exists():
            return _empty_result()

    with _exclusive_file_lock(support / "migration"):
        return _migrate_locked(
            legacy=legacy,
            canonical=canonical,
            support=support,
            journal_path=journal_path,
            backup=backup,
            stage=stage,
            crash_after=_crash_after,
        )


def rollback_legacy_state(
    *,
    home: str | Path | None = None,
) -> StateMigrationResult:
    """Restore the verified migration backup to the legacy root."""
    root = _resolved_home(home)
    legacy, canonical, support, journal_path, backup, stage = _paths(root)
    del canonical
    if not journal_path.is_file():
        raise ValueError(
            "No GigaLoom state migration journal is available to roll back."
        )

    with _exclusive_file_lock(support / "migration"):
        journal = _read_journal(journal_path)
        if journal["phase"] not in {"complete", "promoted", "rolled_back"}:
            raise ValueError(
                "State migration has not reached a verified promotion; retry "
                "'giga state migrate' before rollback."
            )
        verified = _verified_journal_backup(journal, backup)
        already_restored = legacy.is_dir() and _tree_matches_backup(
            legacy,
            backup,
            support,
        )
        if not already_restored:
            restore_stage = support / f".rollback-{uuid4().hex}"
            try:
                copy_verified_tree(backup, restore_stage, verified.sha256)
                _publish_restored_state(
                    restore_stage,
                    legacy,
                    replace=legacy.exists(),
                )
            finally:
                _remove_owned_stage(restore_stage)
        journal = {
            **journal,
            "phase": "rolled_back",
        }
        _write_journal(journal_path, journal)
        _remove_owned_stage(stage)
        return _result_from_journal(
            journal,
            status="rolled_back",
            applied=not already_restored,
            verified=verified,
        )


def _migrate_locked(
    *,
    legacy: Path,
    canonical: Path,
    support: Path,
    journal_path: Path,
    backup: Path,
    stage: Path,
    crash_after: str | None,
) -> StateMigrationResult:
    journal = _read_journal(journal_path) if journal_path.is_file() else None
    resumed = journal is not None

    if journal is None:
        if canonical.exists() and legacy.exists():
            raise _root_collision_error()
        if canonical.exists() or not legacy.exists():
            return _empty_result()
        journal = {
            "schema_version": STATE_MIGRATION_SCHEMA_VERSION,
            "migration_id": STATE_MIGRATION_ID,
            "phase": "planned",
            "source_root": LEGACY_STATE_RELATIVE_PATH.as_posix(),
            "target_root": CANONICAL_STATE_RELATIVE_PATH.as_posix(),
            "backup_root": (
                MIGRATION_SUPPORT_RELATIVE_PATH / _BACKUP_RELATIVE_PATH
            ).as_posix(),
            "backup_sha256": None,
            "file_count": 0,
            "total_bytes": 0,
            "runtime_schema_version": None,
        }
        _write_journal(journal_path, journal)
        _inject_crash(crash_after, "planned")

    phase = str(journal["phase"])
    if phase == "rolled_back":
        raise ValueError(
            "The state cutover was rolled back. Preserve both roots, then resolve "
            "explicitly before running GigaLoom 0.6 again."
        )
    if phase == "complete":
        if not canonical.is_dir() or not legacy.is_dir():
            raise ValueError(
                "Completed migration journal does not match the state roots; "
                "restore the verified backup or resolve the roots explicitly."
            )
        verified = _verified_journal_backup(journal, backup)
        return _result_from_journal(
            journal,
            status="current",
            applied=False,
            verified=verified,
        )
    if not legacy.is_dir():
        raise ValueError(
            "Legacy state disappeared during migration; restore it from the "
            "verified migration backup before retrying."
        )

    if phase == "planned":
        verified = _ensure_source_backup(legacy, backup, support)
        journal = {
            **journal,
            "phase": "backed_up",
            "backup_sha256": verified.sha256,
            "file_count": verified.file_count,
            "total_bytes": verified.total_bytes,
            "runtime_schema_version": verified.runtime_schema_version,
        }
        _write_journal(journal_path, journal)
        _inject_crash(crash_after, "backed_up")
        phase = "backed_up"

    verified = _verified_journal_backup(journal, backup)
    if phase == "backed_up":
        if not _tree_matches_backup(legacy, backup, support):
            raise ValueError(
                "Legacy state changed after its migration backup was created; "
                "stop all old Harness processes and resolve explicitly."
            )
        if canonical.exists():
            raise _root_collision_error()
        _remove_owned_stage(stage)
        copy_verified_tree(backup, stage, verified.sha256)
        if not _tree_matches_backup(stage, backup, support):
            raise ValueError("Staged canonical state does not match its backup.")
        journal = {**journal, "phase": "staged"}
        _write_journal(journal_path, journal)
        _inject_crash(crash_after, "staged")
        phase = "staged"

    if phase == "staged":
        if not _tree_matches_backup(legacy, backup, support):
            raise ValueError(
                "Legacy state changed while canonical migration was staged; "
                "preserve the verified backup and resolve explicitly."
            )
        if canonical.exists():
            if not _tree_matches_backup(canonical, backup, support):
                raise _root_collision_error()
            _remove_owned_stage(stage)
        else:
            if not stage.is_dir():
                raise ValueError(
                    "Migration staging root is missing; retry from the verified backup."
                )
            stage.replace(canonical)
        journal = {**journal, "phase": "promoted"}
        _write_journal(journal_path, journal)
        _inject_crash(crash_after, "promoted")
        phase = "promoted"

    if phase == "promoted":
        if not canonical.is_dir() or not _tree_matches_backup(
            canonical,
            backup,
            support,
        ):
            raise ValueError(
                "Promoted canonical state failed backup verification; restore the "
                "verified legacy backup before startup."
            )
        journal = {**journal, "phase": "complete"}
        _write_journal(journal_path, journal)
        _inject_crash(crash_after, "complete")

    return _result_from_journal(
        journal,
        status="current" if resumed else "migrated",
        applied=not resumed,
        verified=verified,
    )


def _ensure_source_backup(source: Path, backup: Path, support: Path):
    if backup.exists():
        verified = verify_tree(backup)
        if not _tree_matches_backup(source, backup, support):
            raise ValueError(
                "Legacy state changed after its migration backup was created; "
                "preserve both and resolve explicitly."
            )
        return verified
    if backup.parent.is_symlink() or (
        backup.parent.exists() and not backup.parent.is_dir()
    ):
        raise ValueError("Migration backup parent must be a real directory.")
    backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = backup.parent / f".{backup.name}-{uuid4().hex}"
    try:
        created = snapshot_tree(source, temporary)
        temporary.replace(backup)
    finally:
        _remove_owned_stage(temporary)
    verified = verify_tree(backup)
    if created.sha256 != verified.sha256:
        raise ValueError("Migration backup changed after creation.")
    return verified


def _tree_matches_backup(source: Path, backup: Path, support: Path) -> bool:
    candidate = support / f".verify-{uuid4().hex}"
    try:
        created = snapshot_tree(source, candidate)
        verified = verify_tree(backup)
        return created.sha256 == verified.sha256
    finally:
        _remove_owned_stage(candidate)


def _verified_journal_backup(journal: Mapping[str, object], backup: Path):
    expected = journal.get("backup_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("State migration journal has no verified backup digest.")
    verified = verify_tree(backup)
    if verified.sha256 != expected:
        raise ValueError("State migration backup does not match its journal digest.")
    return verified


def _read_journal(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("State migration journal is unreadable.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != STATE_MIGRATION_SCHEMA_VERSION
        or payload.get("migration_id") != STATE_MIGRATION_ID
        or payload.get("phase") not in _PHASES
        or payload.get("source_root") != LEGACY_STATE_RELATIVE_PATH.as_posix()
        or payload.get("target_root") != CANONICAL_STATE_RELATIVE_PATH.as_posix()
    ):
        raise ValueError("State migration journal fields are invalid.")
    return payload


def _write_journal(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _result_from_journal(
    journal: Mapping[str, object],
    *,
    status: Literal["current", "migrated", "rolled_back"],
    applied: bool,
    verified: TreeEvidence,
) -> StateMigrationResult:
    return StateMigrationResult(
        status=status,
        applied=applied,
        source_root=str(journal["source_root"]),
        target_root=str(journal["target_root"]),
        backup_root=str(journal["backup_root"]),
        backup_sha256=verified.sha256,
        file_count=verified.file_count,
        total_bytes=verified.total_bytes,
        runtime_schema_version=verified.runtime_schema_version,
    )


def _empty_result() -> StateMigrationResult:
    return StateMigrationResult(
        status="current",
        applied=False,
        source_root=LEGACY_STATE_RELATIVE_PATH.as_posix(),
        target_root=CANONICAL_STATE_RELATIVE_PATH.as_posix(),
        backup_root=None,
        backup_sha256=None,
        file_count=0,
        total_bytes=0,
        runtime_schema_version=None,
    )


def _root_collision_error() -> ValueError:
    return ValueError(
        "Both ~/.gpt2giga/harness and ~/.gigaloom exist without completed "
        "migration evidence. Preserve both roots. To migrate the legacy root, run "
        "'mv ~/.gigaloom ~/.gigaloom.pre-cutover && giga state migrate'; to keep "
        "the canonical root, run 'mv ~/.gpt2giga/harness "
        "~/.gpt2giga/harness.pre-cutover' before restarting."
    )


def _paths(root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
    support = root / MIGRATION_SUPPORT_RELATIVE_PATH
    return (
        root / LEGACY_STATE_RELATIVE_PATH,
        root / CANONICAL_STATE_RELATIVE_PATH,
        support,
        support / _JOURNAL_RELATIVE_PATH,
        support / _BACKUP_RELATIVE_PATH,
        root / _STAGE_RELATIVE_PATH,
    )


def _resolved_home(home: str | Path | None) -> Path:
    return Path.home().resolve() if home is None else Path(home).expanduser().resolve()


def _remove_owned_stage(stage: Path) -> None:
    if stage.is_symlink():
        raise ValueError("Migration staging root must not be a symbolic link.")
    if stage.exists():
        if not stage.is_dir():
            raise ValueError("Migration staging root is not a directory.")
        shutil.rmtree(stage)


def _inject_crash(requested: str | None, phase: str) -> None:
    if requested == phase:
        raise InjectedStateMigrationCrash(f"injected crash after {phase}")


@contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(f".{path.name}.lock")
    parent = lock_path.parent
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ValueError("Migration support root must be a real directory.")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = str(lock_path.resolve())
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _lock_descriptor(descriptor)
            yield
        finally:
            _unlock_descriptor(descriptor)
            os.close(descriptor)


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


__all__ = [
    "CANONICAL_STATE_RELATIVE_PATH",
    "InjectedStateMigrationCrash",
    "LEGACY_STATE_RELATIVE_PATH",
    "MIGRATION_SUPPORT_RELATIVE_PATH",
    "STATE_MIGRATION_ID",
    "STATE_MIGRATION_SCHEMA_VERSION",
    "StateMigrationResult",
    "migrate_legacy_state",
    "prepare_runtime_state",
    "reject_legacy_state_override",
    "rollback_legacy_state",
]
