from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    LaunchResolutionContextV1,
    ProjectCatalogConflictError,
    ProjectCatalogService,
    ProjectLaunchProfileService,
    launch_profile_from_dict,
    launch_profile_to_dict,
)


def _services(tmp_path):
    data_dir = tmp_path / "data" / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(data_dir / "catalog")
    catalog_service = ProjectCatalogService(
        catalog_repository,
        clock=lambda: datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc),
    )
    profile_repository = FilesystemLaunchProfileRepository(data_dir / "launch_profiles")
    return (
        catalog_service,
        catalog_repository,
        profile_repository,
        ProjectLaunchProfileService(profile_repository, catalog_repository),
    )


def test_launch_profile_crud_is_project_scoped_and_contains_only_soft_hints(tmp_path):
    catalog, _, repository, profiles = _services(tmp_path)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = catalog.add_project(first_root, display_name="First")
    second = catalog.add_project(second_root, display_name="Second")

    profile = profiles.create_profile(
        first.catalog_project_id,
        display_name="Review",
        agent_hint="codex",
        structured_route_hint="codex.app-server",
        model_hint="gpt-5.6",
        mode_hint="review",
        host_hint="local",
        workspace_policy_hint="worktree",
        terminal_mode_hint="managed",
    )

    assert repository.list_page(first.catalog_project_id).items == (profile,)
    assert repository.list_page(second.catalog_project_id).items == ()
    payload = launch_profile_to_dict(profile)
    assert launch_profile_from_dict(payload) == profile
    assert "credentials" not in payload
    assert "environment" not in payload
    assert "args" not in payload

    updated = profiles.update_profile(
        profile.launch_profile_id,
        expected_revision=profile.revision,
        display_name="Direct review",
        structured_route_hint=None,
        terminal_mode_hint="direct",
    )
    assert updated.display_name == "Direct review"
    assert updated.structured_route_hint is None
    assert updated.terminal_mode_hint == "direct"

    deleted = profiles.delete_profile(
        updated.launch_profile_id,
        expected_revision=updated.revision,
    )
    assert deleted == updated
    assert repository.list_page(first.catalog_project_id).items == ()


def test_launch_profile_resolution_never_substitutes_unavailable_hints(tmp_path):
    catalog, _, _, profiles = _services(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    project = catalog.add_project(workspace, display_name="Demo")
    profile = profiles.create_profile(
        project.catalog_project_id,
        display_name="Stale defaults",
        agent_hint="missing-agent",
        structured_route_hint="missing.route",
        model_hint="missing-model",
        mode_hint="review",
        host_hint="remote",
        workspace_policy_hint="worktree",
        terminal_mode_hint="managed",
    )

    resolved = profiles.resolve_profile(
        profile.launch_profile_id,
        LaunchResolutionContextV1(
            agent_ids=frozenset({"codex"}),
            structured_route_ids=frozenset({"codex.app-server"}),
            model_ids=frozenset({"gpt-5.6"}),
            modes=frozenset({"review"}),
            host_ids=frozenset({"local"}),
            workspace_policies=frozenset({"worktree"}),
            terminal_modes=frozenset({"direct"}),
        ),
    )

    assert resolved.agent_id is None
    assert resolved.structured_route_id is None
    assert resolved.model_id is None
    assert resolved.mode == "review"
    assert resolved.host_id is None
    assert resolved.workspace_policy == "worktree"
    assert resolved.terminal_mode is None
    assert resolved.authority_granted is False
    assert {hint.field for hint in resolved.unsatisfied_hints} == {
        "agent_hint",
        "structured_route_hint",
        "model_hint",
        "host_hint",
        "terminal_mode_hint",
    }


def test_launch_profile_rejects_tombstoned_project_and_stale_update(tmp_path):
    catalog, _, _, profiles = _services(tmp_path)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    project = catalog.add_project(workspace, display_name="Demo")
    profile = profiles.create_profile(
        project.catalog_project_id,
        display_name="Default",
    )

    with pytest.raises(ProjectCatalogConflictError, match="revision"):
        profiles.update_profile(
            profile.launch_profile_id,
            expected_revision=profile.revision + 1,
            agent_hint="codex",
        )

    tombstoned = catalog.remove_project(
        project.catalog_project_id,
        expected_revision=project.revision,
    )
    assert tombstoned.state == "tombstoned"
    with pytest.raises(ProjectCatalogConflictError, match="tombstoned"):
        profiles.create_profile(
            project.catalog_project_id,
            display_name="Late profile",
        )
