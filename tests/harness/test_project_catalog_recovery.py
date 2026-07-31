from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from gigaloom.projects.api import (
    FilesystemProjectCatalogRepository,
    InjectedProjectCatalogMigrationCrash,
    ProjectCatalogConflictError,
    ProjectCatalogMigrationService,
    ProjectCatalogService,
)
from gigaloom.sessions import FilesystemHarnessSessionStore


def _filesystem_migration(tmp_path):
    active_state = tmp_path / "state"
    store = FilesystemHarnessSessionStore(active_state)
    repository = FilesystemProjectCatalogRepository(
        active_state / "projects" / "catalog"
    )
    migration = ProjectCatalogMigrationService(
        repository,
        store,
        active_state_dir=active_state,
        backup_root=tmp_path / "backups",
        clock=lambda: datetime(2026, 7, 31, 13, 0, tzinfo=timezone.utc),
    )
    return store, repository, migration


def _legacy_sessions(tmp_path, store):
    resolved = tmp_path / "repo"
    resolved.mkdir()
    missing = tmp_path / "missing"
    first = store.create_session(
        workspace=str(resolved),
        metadata={"project_id": "legacy-first", "marker": "one"},
    )
    second = store.create_session(
        workspace=str(resolved),
        metadata={"project_id": "legacy-second", "marker": "two"},
    )
    third = store.create_session(
        workspace=str(missing),
        metadata={"project_id": "legacy-missing", "marker": "three"},
    )
    return first, second, third


@pytest.mark.parametrize("crash_after_step", range(1, 6))
def test_migration_resumes_after_every_authoritative_write_boundary(
    tmp_path,
    crash_after_step,
):
    store, repository, migration = _filesystem_migration(tmp_path)
    sessions = _legacy_sessions(tmp_path, store)

    with pytest.raises(InjectedProjectCatalogMigrationCrash):
        migration.migrate(crash_after_step=crash_after_step)

    receipt = migration.migrate()

    assert receipt.projects_created == 2
    assert receipt.sessions_migrated == 3
    assert len(repository.entries_for_migration()) == 2
    migrated = tuple(store.get_session(session.id) for session in sessions)
    assert all("project_id" not in session.metadata for session in migrated)
    assert all("catalog_project_id" in session.metadata for session in migrated)
    assert (
        migrated[0].metadata["catalog_project_id"]
        == migrated[1].metadata["catalog_project_id"]
    )
    assert migration.migrate() == receipt


def test_migration_rollback_restores_backup_before_post_migration_write(tmp_path):
    store, repository, migration = _filesystem_migration(tmp_path)
    sessions = _legacy_sessions(tmp_path, store)
    original = {session.id: dict(session.metadata) for session in sessions}
    migration.migrate()

    migration.rollback()

    assert repository.entries_for_migration() == ()
    for session in sessions:
        assert store.get_session(session.id).metadata == original[session.id]
    migration.rollback()
    with pytest.raises(ProjectCatalogConflictError, match="rolled back"):
        migration.migrate()


def test_rollback_rejects_session_write_without_partial_restore(tmp_path):
    store, repository, migration = _filesystem_migration(tmp_path)
    first, second, third = _legacy_sessions(tmp_path, store)
    migration.migrate()
    store.update_session(first.id, title="User changed title after migration")
    before = {
        session.id: dict(store.get_session(session.id).metadata)
        for session in (first, second, third)
    }

    with pytest.raises(ProjectCatalogConflictError, match="session changed"):
        migration.rollback()

    assert len(repository.entries_for_migration()) == 2
    for session in (first, second, third):
        assert store.get_session(session.id).metadata == before[session.id]


def test_rollback_preflights_catalog_drift_before_restoring_sessions(tmp_path):
    store, repository, migration = _filesystem_migration(tmp_path)
    first, second, third = _legacy_sessions(tmp_path, store)
    migration.migrate()
    entry = repository.entries_for_migration()[0]
    catalog = ProjectCatalogService(
        repository,
        clock=lambda: datetime(2026, 7, 31, 13, 1, tzinfo=timezone.utc),
    )
    catalog.rename_project(
        entry.catalog_project_id,
        "Changed after migration",
        expected_revision=entry.revision,
    )
    before = {
        session.id: dict(store.get_session(session.id).metadata)
        for session in (first, second, third)
    }

    with pytest.raises(ProjectCatalogConflictError, match="catalog changed"):
        migration.rollback()

    for session in (first, second, third):
        assert store.get_session(session.id).metadata == before[session.id]


def test_migration_rejects_changed_backup_and_receipt(tmp_path):
    store, _, migration = _filesystem_migration(tmp_path)
    _legacy_sessions(tmp_path, store)
    with pytest.raises(InjectedProjectCatalogMigrationCrash):
        migration.migrate(crash_after_step=1)
    backup = next((tmp_path / "backups").glob("*.json"))
    backup.write_bytes(backup.read_bytes() + b"tampered")

    with pytest.raises(ProjectCatalogConflictError, match="backup changed"):
        migration.migrate()

    other_root = tmp_path / "other"
    other_store, _, other_migration = _filesystem_migration(other_root)
    _legacy_sessions(other_root, other_store)
    other_migration.migrate()
    receipt = json.loads(other_migration.receipt_path.read_text(encoding="utf-8"))
    receipt["projects_created"] += 1
    other_migration.receipt_path.write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="receipt digest mismatch"):
        other_migration.migrate()


def test_migration_reuses_existing_catalog_entry_and_filesystem_read_index(tmp_path):
    store, repository, migration = _filesystem_migration(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    catalog = ProjectCatalogService(
        repository,
        clock=lambda: datetime(2026, 7, 31, 12, 59, tzinfo=timezone.utc),
    )
    existing = catalog.add_project(workspace, display_name="Existing")
    session = store.create_session(
        workspace=str(workspace),
        metadata={"project_id": "legacy-project"},
    )

    receipt = migration.migrate()

    assert receipt.projects_created == 0
    migrated = store.get_session(session.id)
    assert migrated.metadata["catalog_project_id"] == existing.catalog_project_id
    reopened = FilesystemHarnessSessionStore(tmp_path / "state")
    assert {
        item.id
        for item in reopened.list_sessions(
            project_id=existing.catalog_project_id,
            include_archived=True,
        )
    } == {session.id}


def test_migration_prefers_existing_canonical_binding_over_legacy_dual_write(
    tmp_path,
):
    store, repository, migration = _filesystem_migration(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    catalog = ProjectCatalogService(
        repository,
        clock=lambda: datetime(2026, 7, 31, 12, 59, tzinfo=timezone.utc),
    )
    existing = catalog.add_project(workspace, display_name="Existing")
    session = store.create_session(
        workspace=str(workspace),
        metadata={
            "catalog_project_id": existing.catalog_project_id,
            "project_id": "stale-legacy-project",
        },
    )

    migration.migrate()

    assert store.get_session(session.id).metadata["catalog_project_id"] == (
        existing.catalog_project_id
    )
    assert "project_id" not in store.get_session(session.id).metadata
