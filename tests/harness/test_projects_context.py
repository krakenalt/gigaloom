from gpt2giga_harness import bootstrap as legacy_bootstrap
from gpt2giga_harness import editor as legacy_editor
from gpt2giga_harness import environment_actions as legacy_environment_actions
from gpt2giga_harness import environment_pull_requests as legacy_pull_requests
from gpt2giga_harness import environment_push as legacy_environment_push
from gpt2giga_harness import environments as legacy_environments
from gpt2giga_harness import github_environments as legacy_github_environments
from gpt2giga_harness import project as legacy_project
from gpt2giga_harness import project_memory as legacy_memory
from gpt2giga_harness import state_backup as legacy_backup
from gpt2giga_harness import workspace as legacy_workspace
from gpt2giga_harness import worktrees as legacy_worktrees
from gpt2giga_harness.projects import api
from gpt2giga_harness.projects import backup
from gpt2giga_harness.projects import bootstrap
from gpt2giga_harness.projects import memory
from gpt2giga_harness.projects.environment import commit
from gpt2giga_harness.projects.environment import editor
from gpt2giga_harness.projects.environment import github
from gpt2giga_harness.projects.environment import pull_request
from gpt2giga_harness.projects.environment import push
from gpt2giga_harness.projects.environment import registry
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


def test_legacy_environment_imports_alias_the_bounded_context() -> None:
    assert legacy_environments.EnvironmentProviderRegistry is (
        registry.EnvironmentProviderRegistry
    )
    assert legacy_environment_actions.EnvironmentCommitService is (
        commit.EnvironmentCommitService
    )
    assert legacy_environment_push.EnvironmentPushService is push.EnvironmentPushService
    assert legacy_pull_requests.EnvironmentPullRequestService is (
        pull_request.EnvironmentPullRequestService
    )
    assert legacy_github_environments.GitHubEnvironmentService is (
        github.GitHubEnvironmentService
    )
    assert legacy_editor.EditorOpenPlan is editor.EditorOpenPlan
    assert api.EnvironmentProviderRegistry is registry.EnvironmentProviderRegistry
    assert api.EnvironmentCommitService is commit.EnvironmentCommitService
    assert api.EnvironmentPushService is push.EnvironmentPushService
    assert api.EnvironmentPullRequestService is (
        pull_request.EnvironmentPullRequestService
    )
    assert api.GitHubEnvironmentService is github.GitHubEnvironmentService
    assert api.EditorOpenPlan is editor.EditorOpenPlan


def test_projects_api_exposes_config_state_and_memory(tmp_path) -> None:
    project = api.resolve_project(tmp_path, data_dir=tmp_path / "data")
    config = api.init_project_config(tmp_path, project_name="bounded-context")
    state = api.update_project_state(project, {"last_harness": "echo"})
    store = api.FilesystemProjectMemoryStore()
    entry = store.add(project, text="Keep project state local", tags=("project",))

    assert config.project_name == "bounded-context"
    assert state.last_harness == "echo"
    assert store.list(project) == (entry,)
