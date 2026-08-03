from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from gigaloom.projects import api


def test_nested_agent_instructions_have_owner_precedence_and_token_estimates(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "root-private-instruction\n")
    _write(repo / "src/feature/AGENTS.md", "nested-private-instruction\n")
    _commit_all(repo)
    discovery = api.discover_project_instructions(repo)

    projection = api.compile_effective_instructions(
        discovery,
        target_path="src/feature/service.py",
        selected_materialization_owners=("agent_adapter",),
        materialization_revisions={"agent_adapter": "codex-agents-v1"},
        expected_materialization_revisions={"agent_adapter": "codex-agents-v1"},
    )

    included = [
        item for item in projection.sources if item.disposition.value == "include"
    ]
    assert {item.relative_path: item.precedence for item in included} == {
        "AGENTS.md": 0,
        "src/feature/AGENTS.md": 2,
    }
    assert {item.reason.value for item in included} == {"mandatory_instruction"}
    assert projection.lens.token_summary.known_scope_count == 2
    assert projection.lens.token_summary.unknown_scope_count == 0
    assert projection.uncertainties == ()
    assert projection.conflicts == ()
    assert projection.launch_ready is True
    serialized = json.dumps(projection.to_dict(), sort_keys=True)
    assert "private-instruction" not in serialized
    assert projection.read_only is True
    assert projection.auto_materialized is False


def test_cross_owner_overlap_is_visible_and_never_auto_merged(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "agent-private-instruction\n")
    _write(repo / "GEMINI.md", "gemini-private-instruction\n")
    _commit_all(repo)
    discovery = api.discover_project_instructions(repo)

    projection = api.compile_effective_instructions(
        discovery,
        target_path="src/example.py",
        selected_materialization_owners=("agent_adapter", "gemini_adapter"),
        materialization_revisions={
            "agent_adapter": "agents-v1",
            "gemini_adapter": "gemini-project-v1",
        },
    )

    assert [item.kind.value for item in projection.conflicts] == [
        "cross_owner_scope_overlap"
    ]
    assert projection.conflicts[0].resolution == (
        "owner_specific_precedence_no_auto_merge"
    )
    assert any(
        item.kind.value == "owner_precedence_unknown"
        and item.materialization_owner == "gemini_adapter"
        for item in projection.uncertainties
    )
    assert projection.launch_ready is False


def test_owner_scope_and_prompt_selection_produce_explicit_omissions(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "agent\n")
    _write(repo / "GEMINI.md", "gemini\n")
    _write(
        repo / ".giga/harness.toml",
        "[presets.plan]\n"
        'title = "Plan"\n'
        'prompt_file = ".giga/prompts/plan.md"\n'
        "[presets.ask]\n"
        'title = "Ask"\n'
        'prompt = "inline-private-prompt"\n',
    )
    _write(repo / ".giga/prompts/plan.md", "selected-private-prompt\n")
    _commit_all(repo)
    discovery = api.discover_project_instructions(repo)
    prompt = next(
        item
        for item in discovery.sources
        if item.kind is api.ProjectInstructionKind.PROJECT_PROMPT
    )

    projection = api.compile_effective_instructions(
        discovery,
        selected_materialization_owners=("gigaloom_projects",),
        selected_source_ids=(prompt.source_id,),
        materialization_revisions={"gigaloom_projects": "project-presets-v1"},
    )

    by_path = {item.relative_path: item for item in projection.sources}
    assert by_path[".giga/prompts/plan.md"].disposition.value == "include"
    assert by_path[".giga/prompts/plan.md"].reason.value == "user_selection"
    assert by_path[".giga/harness.toml"].disposition.value == "omit"
    assert by_path[".giga/harness.toml"].reason.value == "excluded_by_policy"
    assert by_path["AGENTS.md"].reason.value == "unsupported"
    assert by_path["GEMINI.md"].reason.value == "unsupported"
    assert projection.lens.is_partial is True


def test_stale_adapter_revision_fails_closed_with_visible_uncertainty(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "agent\n")
    _commit_all(repo)
    discovery = api.discover_project_instructions(repo)

    projection = api.compile_effective_instructions(
        discovery,
        selected_materialization_owners=("agent_adapter",),
        materialization_revisions={"agent_adapter": "agents-v2"},
        expected_materialization_revisions={"agent_adapter": "agents-v1"},
    )

    source = projection.sources[0]
    assert source.disposition.value == "omit"
    assert source.freshness.value == "stale"
    assert source.reason.value == "stale"
    assert any(
        item.kind.value == "stale_adapter_revision" for item in projection.uncertainties
    )
    assert projection.launch_ready is False


def test_target_path_and_revision_bindings_are_bounded(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "agent\n")
    _commit_all(repo)
    discovery = api.discover_project_instructions(repo)

    with pytest.raises(ValueError, match="target_path"):
        api.compile_effective_instructions(discovery, target_path="../private")
    with pytest.raises(ValueError, match="materialization revision"):
        api.compile_effective_instructions(
            discovery,
            materialization_revisions={"agent_adapter": "bad\nrevision"},
        )


def _repository(tmp_path: Path) -> Path:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run((git, "init", "-q", "-b", "main"), cwd=repo, check=True)
    _write(repo / "src/example.py", "VALUE = 1\n")
    return repo


def _commit_all(repo: Path) -> None:
    git = shutil.which("git")
    assert git is not None
    subprocess.run((git, "add", "."), cwd=repo, check=True)
    subprocess.run(
        (
            git,
            "-c",
            "user.name=Instruction Impact Fixture",
            "-c",
            "user.email=instruction-impact@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ),
        cwd=repo,
        check=True,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
