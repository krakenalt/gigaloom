"""One-time, backup-gated migration from legacy session project bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from gigaloom.projects.resolution import project_id_for_root

from .codec import (
    catalog_entry_from_dict,
    catalog_entry_to_dict,
    with_catalog_entry_digest,
)
from .errors import ProjectCatalogConflictError, ProjectCatalogNotFoundError
from .migration_io import (
    canonical_migration_json,
    migration_file_digest,
    read_migration_json,
    write_migration_json,
    write_new_migration_json,
)
from .models import MAX_CATALOG_ENTRIES, ProjectCatalogEntryV1, ProjectLocationRef
from .repository import FilesystemProjectCatalogRepository, _exclusive_file_lock
from .service import normalize_display_name, resolved_project_location

PROJECT_CATALOG_MIGRATION_ID = "project_catalog_v1"
PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION = 1
MAX_MIGRATION_SESSIONS = 10_000


class InjectedProjectCatalogMigrationCrash(RuntimeError):
    """Test-only interruption raised at a durable migration boundary."""


class MigratableSession(Protocol):
    """Minimum authoritative session projection required by migration."""

    id: str
    updated_at: str
    workspace: str | None
    metadata: Mapping[str, Any]


class ProjectCatalogMigrationSessionStore(Protocol):
    """Atomic public session-store surface consumed by the migration."""

    def list_sessions(
        self,
        *,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> tuple[MigratableSession, ...]: ...

    def get_session(self, session_id: str) -> MigratableSession: ...

    def update_session_if_revision(
        self,
        session_id: str,
        expected_updated_at: str,
        **patch: Any,
    ) -> MigratableSession | None: ...


@dataclass(frozen=True)
class ProjectCatalogMigrationReceiptV1:
    """Content-free evidence for the one-time catalog binding migration."""

    migration_id: str
    status: str
    projects_created: int
    unresolved_projects: int
    sessions_migrated: int
    sessions_unfiled: int
    sessions_already_migrated: int
    source_digest: str
    backup_digest: str
    omissions: tuple[str, ...]
    completed_at: str
    receipt_digest: str
    schema_version: int = PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Serialize the receipt without paths or session content."""
        return {
            "schema_version": self.schema_version,
            "migration_id": self.migration_id,
            "status": self.status,
            "projects_created": self.projects_created,
            "unresolved_projects": self.unresolved_projects,
            "sessions_migrated": self.sessions_migrated,
            "sessions_unfiled": self.sessions_unfiled,
            "sessions_already_migrated": self.sessions_already_migrated,
            "source_digest": self.source_digest,
            "backup_digest": self.backup_digest,
            "omissions": list(self.omissions),
            "completed_at": self.completed_at,
            "receipt_digest": self.receipt_digest,
        }


class ProjectCatalogMigrationService:
    """Prepare, apply, resume, and roll back one catalog migration."""

    def __init__(
        self,
        catalog_repository: FilesystemProjectCatalogRepository,
        session_store: ProjectCatalogMigrationSessionStore,
        *,
        active_state_dir: str | Path,
        backup_root: str | Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.catalog_repository = catalog_repository
        self.session_store = session_store
        self.active_state_dir = Path(active_state_dir).expanduser().resolve()
        self.backup_root = Path(backup_root).expanduser().resolve()
        if _is_relative_to(self.backup_root, self.active_state_dir):
            raise ValueError("migration backup_root must be outside active state")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        migrations_dir = self.catalog_repository.catalog_dir / "migrations"
        self.journal_path = migrations_dir / f"{PROJECT_CATALOG_MIGRATION_ID}.json"
        self.receipt_path = (
            migrations_dir / f"{PROJECT_CATALOG_MIGRATION_ID}.receipt.json"
        )
        self.lock_path = migrations_dir / PROJECT_CATALOG_MIGRATION_ID

    def migrate(
        self,
        *,
        crash_after_step: int | None = None,
    ) -> ProjectCatalogMigrationReceiptV1:
        """Apply or resume the deterministic migration after a verified backup."""
        with _exclusive_file_lock(self.lock_path):
            journal = (
                read_migration_json(self.journal_path)
                if self.journal_path.exists()
                else self._prepare_journal()
            )
            _validate_journal(journal)
            if journal["status"] == "rolled_back":
                raise ProjectCatalogConflictError(
                    "project catalog migration was rolled back"
                )
            if journal["status"] == "completed":
                return _receipt_from_dict(read_migration_json(self.receipt_path))
            backup_path = Path(str(journal["backup_path"])).resolve()
            if not _is_relative_to(backup_path, self.backup_root):
                raise ProjectCatalogConflictError(
                    "project catalog backup path escaped its configured root"
                )
            if migration_file_digest(backup_path) != journal["backup_digest"]:
                raise ProjectCatalogConflictError("project catalog backup changed")
            step = 0
            for item in journal["projects"]:
                entry = catalog_entry_from_dict(item["entry"])
                if item["create"]:
                    try:
                        current = self.catalog_repository.get(entry.catalog_project_id)
                    except ProjectCatalogNotFoundError:
                        self.catalog_repository.create(entry)
                    else:
                        if current != entry:
                            raise ProjectCatalogConflictError(
                                "migration project entry conflicts with current catalog"
                            )
                step = self._migration_step(step, crash_after_step)
            migrated = 0
            for item in journal["sessions"]:
                if item["target_metadata"] == item["original_metadata"]:
                    continue
                current = self.session_store.get_session(str(item["session_id"]))
                if dict(current.metadata) == item["target_metadata"]:
                    item["migrated_updated_at"] = current.updated_at
                elif (
                    current.updated_at == item["original_updated_at"]
                    and dict(current.metadata) == item["original_metadata"]
                ):
                    updated = self.session_store.update_session_if_revision(
                        current.id,
                        current.updated_at,
                        metadata=item["target_metadata"],
                    )
                    if updated is None:
                        raise ProjectCatalogConflictError(
                            "session changed during project catalog migration"
                        )
                    item["migrated_updated_at"] = updated.updated_at
                else:
                    raise ProjectCatalogConflictError(
                        "session changed after project catalog migration planning"
                    )
                migrated += 1
                write_migration_json(self.journal_path, journal)
                step = self._migration_step(step, crash_after_step)
            receipt = self._complete_receipt(journal, migrated=migrated)
            write_migration_json(self.receipt_path, receipt.to_dict())
            journal["status"] = "completed"
            journal["completed_at"] = receipt.completed_at
            write_migration_json(self.journal_path, journal)
            return receipt

    def rollback(self) -> None:
        """Restore bindings only while no post-migration write has occurred."""
        with _exclusive_file_lock(self.lock_path):
            journal = read_migration_json(self.journal_path)
            _validate_journal(journal)
            if journal["status"] == "rolled_back":
                return
            self._preflight_rollback(journal)
            for item in journal["sessions"]:
                current = self.session_store.get_session(str(item["session_id"]))
                target = item["target_metadata"]
                original = item["original_metadata"]
                if dict(current.metadata) == original:
                    continue
                if dict(current.metadata) != target:
                    raise ProjectCatalogConflictError(
                        "session changed after project catalog migration"
                    )
                migrated_revision = item.get("migrated_updated_at")
                if (
                    migrated_revision is not None
                    and current.updated_at != migrated_revision
                ):
                    raise ProjectCatalogConflictError(
                        "session has a post-migration write"
                    )
                restored = self.session_store.update_session_if_revision(
                    current.id,
                    current.updated_at,
                    metadata=original,
                )
                if restored is None:
                    raise ProjectCatalogConflictError(
                        "session changed during project catalog rollback"
                    )
            for item in reversed(journal["projects"]):
                if not item["create"]:
                    continue
                entry = catalog_entry_from_dict(item["entry"])
                try:
                    self.catalog_repository.delete_migration_entry(
                        entry.catalog_project_id,
                        expected_digest=entry.digest,
                    )
                except ProjectCatalogNotFoundError:
                    pass
            journal["status"] = "rolled_back"
            journal["rolled_back_at"] = _utc_text(self._clock())
            write_migration_json(self.journal_path, journal)

    def _preflight_rollback(self, journal: Mapping[str, Any]) -> None:
        """Reject drift before restoring any authoritative session binding."""
        for item in journal["sessions"]:
            current = self.session_store.get_session(str(item["session_id"]))
            target = item["target_metadata"]
            original = item["original_metadata"]
            if dict(current.metadata) == original:
                continue
            if dict(current.metadata) != target:
                raise ProjectCatalogConflictError(
                    "session changed after project catalog migration"
                )
            migrated_revision = item.get("migrated_updated_at")
            if (
                migrated_revision is not None
                and current.updated_at != migrated_revision
            ):
                raise ProjectCatalogConflictError("session has a post-migration write")
        for item in journal["projects"]:
            if not item["create"]:
                continue
            expected = catalog_entry_from_dict(item["entry"])
            try:
                current = self.catalog_repository.get(expected.catalog_project_id)
            except ProjectCatalogNotFoundError:
                continue
            if current.revision != 1 or current.digest != expected.digest:
                raise ProjectCatalogConflictError(
                    "project catalog changed after migration"
                )

    def _prepare_journal(self) -> dict[str, Any]:
        sessions = self.session_store.list_sessions(
            include_archived=True,
            limit=MAX_MIGRATION_SESSIONS + 1,
        )
        if len(sessions) > MAX_MIGRATION_SESSIONS:
            raise ValueError("project catalog migration session bound exceeded")
        projects, bindings, already_migrated = self._build_plan(tuple(sessions))
        source_payload = {
            "catalog_digests": [
                entry.digest
                for entry in self.catalog_repository.entries_for_migration()
            ],
            "sessions": [
                {
                    "session_id": item["session_id"],
                    "updated_at": item["original_updated_at"],
                    "metadata_digest": sha256(
                        canonical_migration_json(item["original_metadata"])
                    ).hexdigest(),
                }
                for item in bindings
            ],
        }
        source_digest = sha256(canonical_migration_json(source_payload)).hexdigest()
        backup_path = self.backup_root / (
            f"{PROJECT_CATALOG_MIGRATION_ID}-{source_digest[:16]}.json"
        )
        backup_payload = {
            "schema_version": PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
            "migration_id": PROJECT_CATALOG_MIGRATION_ID,
            "source_digest": source_digest,
            "sessions": [
                {
                    "session_id": item["session_id"],
                    "updated_at": item["original_updated_at"],
                    "metadata": item["original_metadata"],
                }
                for item in bindings
            ],
        }
        backup_digest = write_new_migration_json(backup_path, backup_payload)
        journal: dict[str, Any] = {
            "schema_version": PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
            "migration_id": PROJECT_CATALOG_MIGRATION_ID,
            "status": "applying",
            "created_at": _utc_text(self._clock()),
            "source_digest": source_digest,
            "backup_path": str(backup_path),
            "backup_digest": backup_digest,
            "sessions_already_migrated": already_migrated,
            "projects": projects,
            "sessions": bindings,
        }
        write_migration_json(self.journal_path, journal)
        return journal

    def _build_plan(
        self,
        sessions: tuple[MigratableSession, ...],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
        existing = self.catalog_repository.entries_for_migration()
        projects_by_key: dict[str, dict[str, Any]] = {}
        bindings: list[dict[str, Any]] = []
        already_migrated = 0
        for session in sorted(sessions, key=lambda item: item.id):
            original = dict(session.metadata)
            current_catalog_id = _optional_text(original.get("catalog_project_id"))
            legacy_project_id = _optional_text(original.get("project_id"))
            if current_catalog_id is not None:
                self.catalog_repository.get(current_catalog_id)
                target_catalog_id = current_catalog_id
                if legacy_project_id is None:
                    already_migrated += 1
            else:
                group = _session_group(session, legacy_project_id)
                if group is None:
                    target_catalog_id = None
                else:
                    key, location, harness_project_id, display_name, state = group
                    existing_entry = _matching_entry(
                        existing, location, harness_project_id
                    )
                    if existing_entry is not None:
                        target_catalog_id = existing_entry.catalog_project_id
                    else:
                        project = projects_by_key.get(key)
                        if project is None:
                            catalog_project_id = _migration_project_id(key)
                            entry = with_catalog_entry_digest(
                                ProjectCatalogEntryV1(
                                    catalog_project_id=catalog_project_id,
                                    display_name=normalize_display_name(display_name),
                                    harness_project_id=harness_project_id,
                                    location=location,
                                    state=state,
                                    created_at=_utc_text(self._clock()),
                                    updated_at=_utc_text(self._clock()),
                                    last_opened_at=None,
                                    revision=1,
                                    digest="0" * 64,
                                )
                            )
                            project = {
                                "create": True,
                                "entry": catalog_entry_to_dict(entry),
                            }
                            projects_by_key[key] = project
                        target_catalog_id = str(project["entry"]["catalog_project_id"])
            target = dict(original)
            target.pop("project_id", None)
            if target_catalog_id is None:
                target.pop("catalog_project_id", None)
            else:
                target["catalog_project_id"] = target_catalog_id
            bindings.append(
                {
                    "session_id": session.id,
                    "original_updated_at": session.updated_at,
                    "original_metadata": original,
                    "target_catalog_project_id": target_catalog_id,
                    "target_metadata": target,
                    "migrated_updated_at": None,
                }
            )
        if len(existing) + len(projects_by_key) > MAX_CATALOG_ENTRIES:
            raise ValueError("project catalog migration entry bound exceeded")
        return list(projects_by_key.values()), bindings, already_migrated

    def _complete_receipt(
        self,
        journal: Mapping[str, Any],
        *,
        migrated: int,
    ) -> ProjectCatalogMigrationReceiptV1:
        completed_at = _utc_text(self._clock())
        projects = journal["projects"]
        payload = {
            "schema_version": PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION,
            "migration_id": PROJECT_CATALOG_MIGRATION_ID,
            "status": "completed",
            "projects_created": sum(bool(item["create"]) for item in projects),
            "unresolved_projects": sum(
                catalog_entry_from_dict(item["entry"]).state == "unresolved"
                for item in projects
            ),
            "sessions_migrated": migrated,
            "sessions_unfiled": sum(
                item["target_catalog_project_id"] is None
                for item in journal["sessions"]
            ),
            "sessions_already_migrated": int(journal["sessions_already_migrated"]),
            "source_digest": str(journal["source_digest"]),
            "backup_digest": str(journal["backup_digest"]),
            "omissions": ["session_content", "repository_content", "credentials"],
            "completed_at": completed_at,
            "receipt_digest": "",
        }
        payload["receipt_digest"] = sha256(
            canonical_migration_json(payload)
        ).hexdigest()
        return _receipt_from_dict(payload)

    @staticmethod
    def _migration_step(current: int, crash_after_step: int | None) -> int:
        result = current + 1
        if crash_after_step is not None and result == crash_after_step:
            raise InjectedProjectCatalogMigrationCrash(
                f"injected project catalog migration crash after step {result}"
            )
        return result


def _session_group(
    session: MigratableSession,
    legacy_project_id: str | None,
) -> tuple[str, ProjectLocationRef, str, str, str] | None:
    if session.workspace:
        requested = Path(session.workspace).expanduser().absolute()
        try:
            location = resolved_project_location(requested)
        except ValueError:
            canonical = requested.resolve(strict=False)
            location = ProjectLocationRef(
                kind="local",
                path=str(requested),
                canonical_path=str(canonical),
                identity=None,
            )
            state = "unresolved"
        else:
            state = "active"
        canonical_path = location.canonical_path or str(requested)
        key = f"location:{os.path.normcase(canonical_path).casefold()}"
        return (
            key,
            location,
            project_id_for_root(canonical_path),
            requested.name or "Unresolved project",
            state,
        )
    if legacy_project_id:
        return (
            f"legacy:{legacy_project_id}",
            ProjectLocationRef("local", None, None, None),
            legacy_project_id,
            f"Unresolved {legacy_project_id[-12:]}",
            "unresolved",
        )
    return None


def _matching_entry(
    entries: tuple[ProjectCatalogEntryV1, ...],
    location: ProjectLocationRef,
    harness_project_id: str,
) -> ProjectCatalogEntryV1 | None:
    location_key = (
        os.path.normcase(location.canonical_path).casefold()
        if location.canonical_path is not None
        else None
    )
    for entry in entries:
        entry_key = (
            os.path.normcase(entry.location.canonical_path).casefold()
            if entry.location.canonical_path is not None
            else None
        )
        if entry.harness_project_id == harness_project_id or (
            location_key is not None and entry_key == location_key
        ):
            if entry.state == "tombstoned":
                raise ProjectCatalogConflictError(
                    "legacy session resolves to a tombstoned catalog project"
                )
            return entry
    return None


def _migration_project_id(key: str) -> str:
    digest = sha256(f"{PROJECT_CATALOG_MIGRATION_ID}\0{key}".encode()).hexdigest()
    return f"prj_{digest[:24]}"


def _validate_journal(journal: Mapping[str, Any]) -> None:
    if journal.get("schema_version") != PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION:
        raise ValueError("unsupported project catalog migration schema_version")
    if journal.get("migration_id") != PROJECT_CATALOG_MIGRATION_ID:
        raise ValueError("unexpected project catalog migration id")
    if journal.get("status") not in {"applying", "completed", "rolled_back"}:
        raise ValueError("invalid project catalog migration status")
    if not isinstance(journal.get("projects"), list) or not isinstance(
        journal.get("sessions"), list
    ):
        raise ValueError("project catalog migration plan is invalid")


def _receipt_from_dict(data: Mapping[str, Any]) -> ProjectCatalogMigrationReceiptV1:
    if data.get("schema_version") != PROJECT_CATALOG_MIGRATION_SCHEMA_VERSION:
        raise ValueError("unsupported project catalog migration receipt schema_version")
    if data.get("migration_id") != PROJECT_CATALOG_MIGRATION_ID:
        raise ValueError("unexpected project catalog migration receipt id")
    if data.get("status") != "completed":
        raise ValueError("invalid project catalog migration receipt status")
    payload = dict(data)
    digest = str(payload.get("receipt_digest") or "")
    payload["receipt_digest"] = ""
    if sha256(canonical_migration_json(payload)).hexdigest() != digest:
        raise ValueError("project catalog migration receipt digest mismatch")
    return ProjectCatalogMigrationReceiptV1(
        schema_version=int(data["schema_version"]),
        migration_id=str(data["migration_id"]),
        status=str(data["status"]),
        projects_created=int(data["projects_created"]),
        unresolved_projects=int(data["unresolved_projects"]),
        sessions_migrated=int(data["sessions_migrated"]),
        sessions_unfiled=int(data["sessions_unfiled"]),
        sessions_already_migrated=int(data["sessions_already_migrated"]),
        source_digest=str(data["source_digest"]),
        backup_digest=str(data["backup_digest"]),
        omissions=tuple(str(item) for item in data["omissions"]),
        completed_at=str(data["completed_at"]),
        receipt_digest=digest,
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("project catalog migration clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
