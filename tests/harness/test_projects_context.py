from gpt2giga_harness import project as legacy_project
from gpt2giga_harness import project_memory as legacy_memory
from gpt2giga_harness.projects import api
from gpt2giga_harness.projects import memory


def test_legacy_project_imports_alias_the_bounded_context() -> None:
    assert legacy_project.resolve_project is api.resolve_project
    assert legacy_project.load_project_config is api.load_project_config
    assert legacy_project.update_project_state is api.update_project_state
    assert (
        legacy_memory.FilesystemProjectMemoryStore
        is memory.FilesystemProjectMemoryStore
    )


def test_projects_api_exposes_config_state_and_memory(tmp_path) -> None:
    project = api.resolve_project(tmp_path, data_dir=tmp_path / "data")
    config = api.init_project_config(tmp_path, project_name="bounded-context")
    state = api.update_project_state(project, {"last_harness": "echo"})
    store = api.FilesystemProjectMemoryStore()
    entry = store.add(project, text="Keep project state local", tags=("project",))

    assert config.project_name == "bounded-context"
    assert state.last_harness == "echo"
    assert store.list(project) == (entry,)
