from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from gigaloom.projects.api import (
    FilesystemProjectCatalogRepository,
    ProjectCatalogConflictError,
    ProjectCatalogMigrationService,
    ProjectCatalogService,
    SessionCatalogBindingService,
)
from gigaloom.sessions import InMemoryHarnessSessionStore


def _migration(tmp_path, store):
    active_state = tmp_path / "state"
    repository = FilesystemProjectCatalogRepository(
        active_state / "projects" / "catalog"
    )
    service = ProjectCatalogMigrationService(
        repository,
        store,
        active_state_dir=active_state,
        backup_root=tmp_path / "migration-backups",
        clock=lambda: datetime(2026, 7, 31, 11, 0, tzinfo=timezone.utc),
    )
    return repository, service


def test_migration_groups_sessions_and_removes_legacy_dual_write(tmp_path):
    store = InMemoryHarnessSessionStore()
    resolved_root = tmp_path / "repo"
    resolved_root.mkdir()
    missing_root = tmp_path / "missing"
    first = store.create_session(
        workspace=str(resolved_root),
        metadata={"project_id": "legacy-one", "safe": "value"},
    )
    second = store.create_session(
        workspace=str(resolved_root),
        metadata={"project_id": "legacy-two"},
    )
    unresolved = store.create_session(
        workspace=str(missing_root),
        metadata={"project_id": "legacy-missing"},
    )
    unfiled = store.create_session(metadata={"safe": "unfiled"})
    repository, migration = _migration(tmp_path, store)

    receipt = migration.migrate()

    assert receipt.projects_created == 2
    assert receipt.unresolved_projects == 1
    assert receipt.sessions_migrated == 3
    assert receipt.sessions_unfiled == 1
    migrated_first = store.get_session(first.id)
    migrated_second = store.get_session(second.id)
    migrated_unresolved = store.get_session(unresolved.id)
    assert "project_id" not in migrated_first.metadata
    assert (
        migrated_first.metadata["catalog_project_id"]
        == migrated_second.metadata["catalog_project_id"]
    )
    assert migrated_first.metadata["safe"] == "value"
    assert (
        repository.get(str(migrated_unresolved.metadata["catalog_project_id"])).state
        == "unresolved"
    )
    assert store.get_session(unfiled.id).metadata["safe"] == "unfiled"
    assert "catalog_project_id" not in store.get_session(unfiled.id).metadata
    bound_sessions = store.list_sessions(
        project_id=str(migrated_first.metadata["catalog_project_id"]),
        include_archived=True,
    )
    assert {item.id for item in bound_sessions} == {first.id, second.id}

    repeated = migration.migrate()
    assert repeated == receipt
    serialized = json.dumps(receipt.to_dict(), sort_keys=True)
    assert str(resolved_root) not in serialized
    assert "safe" not in serialized


def test_binding_service_moves_and_unfiles_without_legacy_project_id(tmp_path):
    store = InMemoryHarnessSessionStore()
    repository, _ = _migration(tmp_path, store)
    catalog = ProjectCatalogService(
        repository,
        clock=lambda: datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc),
    )
    workspace = tmp_path / "repo"
    workspace.mkdir()
    project = catalog.add_project(workspace, display_name="Demo")
    session = store.create_session(metadata={"project_id": "legacy", "keep": True})
    bindings = SessionCatalogBindingService(repository, store)

    moved = bindings.move_session(
        session.id,
        to_catalog_project_id=project.catalog_project_id,
        expected_updated_at=session.updated_at,
    )

    assert moved.metadata["catalog_project_id"] == project.catalog_project_id
    assert moved.metadata["keep"] is True
    assert "project_id" not in moved.metadata
    unfiled = bindings.move_session(
        session.id,
        to_catalog_project_id=None,
        expected_updated_at=moved.updated_at,
    )
    assert unfiled.metadata["keep"] is True
    assert "catalog_project_id" not in unfiled.metadata
    assert "project_id" not in unfiled.metadata

    tombstoned = catalog.remove_project(
        project.catalog_project_id,
        expected_revision=project.revision,
    )
    assert tombstoned.state == "tombstoned"
    with pytest.raises(ProjectCatalogConflictError, match="tombstoned"):
        bindings.move_session(
            session.id,
            to_catalog_project_id=project.catalog_project_id,
            expected_updated_at=unfiled.updated_at,
        )


def test_migration_requires_backup_outside_active_state(tmp_path):
    store = InMemoryHarnessSessionStore()
    active_state = tmp_path / "state"
    repository = FilesystemProjectCatalogRepository(
        active_state / "projects" / "catalog"
    )

    with pytest.raises(ValueError, match="outside active state"):
        ProjectCatalogMigrationService(
            repository,
            store,
            active_state_dir=active_state,
            backup_root=active_state / "backups",
        )
