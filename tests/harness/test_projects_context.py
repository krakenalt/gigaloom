from gpt2giga_harness import bootstrap as legacy_bootstrap
from gpt2giga_harness import project as legacy_project
from gpt2giga_harness import project_memory as legacy_memory
from gpt2giga_harness import state_backup as legacy_backup
from gpt2giga_harness import workspace as legacy_workspace
from gpt2giga_harness import worktrees as legacy_worktrees
from gpt2giga_harness.projects import api
from gpt2giga_harness.projects import backup
from gpt2giga_harness.projects import bootstrap
from gpt2giga_harness.projects import memory
from gpt2giga_harness.projects.workspace import api as workspace_api
from gpt2giga_harness.projects.workspace import worktrees


def test_legacy_project_imports_alias_the_bounded_context() -> None:
    assert legacy_project.resolve_project is api.resolve_project
    assert legacy_project.load_project_config is api.load_project_config
    assert legacy_project.update_project_state is api.update_project_state
    assert (
        legacy_memory.FilesystemProjectMemoryStore
        is memory.FilesystemProjectMemoryStore
    )


def test_legacy_project_state_services_alias_the_bounded_context() -> None:
    assert legacy_bootstrap.BootstrapService is bootstrap.BootstrapService
    assert legacy_backup.create_state_backup is backup.create_state_backup
    assert api.BootstrapService is bootstrap.BootstrapService
    assert api.create_state_backup is backup.create_state_backup


def test_legacy_workspace_imports_alias_the_bounded_context() -> None:
    assert legacy_workspace.resolve_workspace is workspace_api.resolve_workspace
    assert legacy_workspace.workspace_tree is workspace_api.workspace_tree
    assert legacy_worktrees.prepare_workspace_execution is (
        worktrees.prepare_workspace_execution
    )
    assert legacy_worktrees.capture_workspace_diff is worktrees.capture_workspace_diff
    assert api.WorkspacePolicy is workspace_api.WorkspacePolicy
    assert api.workspace_file_metadata is workspace_api.workspace_file_metadata


def test_projects_api_exposes_config_state_and_memory(tmp_path) -> None:
    project = api.resolve_project(tmp_path, data_dir=tmp_path / "data")
    config = api.init_project_config(tmp_path, project_name="bounded-context")
    state = api.update_project_state(project, {"last_harness": "echo"})
    store = api.FilesystemProjectMemoryStore()
    entry = store.add(project, text="Keep project state local", tags=("project",))

    assert config.project_name == "bounded-context"
    assert state.last_harness == "echo"
    assert store.list(project) == (entry,)
