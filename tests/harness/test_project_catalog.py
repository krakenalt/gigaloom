from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from gigaloom.projects.api import (
    FilesystemProjectCatalogRepository,
    ProjectCatalogConflictError,
    ProjectCatalogService,
    catalog_entry_from_dict,
    catalog_entry_to_dict,
)


def _service(tmp_path):
    repository = FilesystemProjectCatalogRepository(
        tmp_path / "data" / "projects" / "catalog"
    )
    service = ProjectCatalogService(
        repository,
        clock=lambda: datetime(2026, 7, 31, 9, 0, tzinfo=timezone.utc),
    )
    return repository, service


def test_catalog_lifecycle_preserves_empty_project_and_repository(tmp_path):
    repository, service = _service(tmp_path)
    workspace = tmp_path / "empty-repository"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("owned by user\n", encoding="utf-8")

    created = service.add_project(workspace, display_name="Empty project")

    assert created.catalog_project_id.startswith("prj_")
    assert created.state == "active"
    assert created.revision == 1
    assert repository.list_page().items == (created,)

    renamed = service.rename_project(
        created.catalog_project_id,
        "Renamed project",
        expected_revision=created.revision,
    )
    assert renamed.display_name == "Renamed project"
    assert renamed.location == created.location

    removed = service.remove_project(
        created.catalog_project_id,
        expected_revision=renamed.revision,
    )
    assert removed.state == "tombstoned"
    assert repository.list_page().items == ()
    assert repository.list_page(include_tombstoned=True).items == (removed,)
    assert marker.read_text(encoding="utf-8") == "owned by user\n"


def test_catalog_rejects_duplicate_symlink_and_casefolded_location(tmp_path):
    _, service = _service(tmp_path)
    workspace = tmp_path / "Repo"
    workspace.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workspace, target_is_directory=True)
    service.add_project(workspace, display_name="First")

    with pytest.raises(ProjectCatalogConflictError, match="already"):
        service.add_project(alias, display_name="Alias")


def test_catalog_pages_are_bounded_and_cursor_ordered(tmp_path):
    repository, service = _service(tmp_path)
    for index in range(4):
        workspace = tmp_path / f"repo-{index}"
        workspace.mkdir()
        service.add_project(workspace, display_name=f"Project {index}")

    first = repository.list_page(limit=2)
    second = repository.list_page(cursor=first.next_cursor, limit=2)

    assert len(first.items) == 2
    assert first.has_more is True
    assert first.next_cursor == first.items[-1].catalog_project_id
    assert len(second.items) == 2
    assert second.has_more is False
    assert {item.catalog_project_id for item in (*first.items, *second.items)} == {
        item.catalog_project_id for item in repository.entries_for_migration()
    }
    with pytest.raises(ValueError, match="limit"):
        repository.list_page(limit=101)


def test_relocation_requires_exact_preview_and_identity_confirmation(tmp_path):
    repository, service = _service(tmp_path)
    original = tmp_path / "old"
    destination = tmp_path / "new"
    original.mkdir()
    destination.mkdir()
    created = service.add_project(original, display_name="Movable")
    preview = service.preview_relocation(
        created.catalog_project_id,
        destination,
        expected_revision=created.revision,
    )

    assert preview.identity_matches is False
    with pytest.raises(ProjectCatalogConflictError, match="confirmation"):
        service.relocate_project(preview, preview_digest=preview.preview_digest)
    with pytest.raises(ProjectCatalogConflictError, match="preview"):
        service.relocate_project(
            preview,
            preview_digest="0" * 64,
            allow_identity_change=True,
        )

    relocated = service.relocate_project(
        preview,
        preview_digest=preview.preview_digest,
        allow_identity_change=True,
    )
    assert relocated.catalog_project_id == created.catalog_project_id
    assert relocated.location.canonical_path == str(destination.resolve())
    assert repository.get(created.catalog_project_id) == relocated


def test_catalog_codec_rejects_digest_tamper_and_unknown_fields(tmp_path):
    _, service = _service(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    created = service.add_project(workspace, display_name="Codec")
    payload = catalog_entry_to_dict(created)

    assert catalog_entry_from_dict(payload) == created

    tampered = json.loads(json.dumps(payload))
    tampered["display_name"] = "Changed"
    with pytest.raises(ValueError, match="digest mismatch"):
        catalog_entry_from_dict(tampered)
    with pytest.raises(ValueError, match="unexpected fields"):
        catalog_entry_from_dict({**payload, "credentials": "forbidden"})
