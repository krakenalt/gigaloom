from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from gigaloom.projects.api import instructions_api as api


def test_discovers_git_visible_nested_rules_and_selected_prompts(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "root-private-instruction\n")
    _write(repo / "src/feature/AGENTS.md", "nested-private-instruction\n")
    _write(repo / "GEMINI.md", "gemini-private-instruction\n")
    _write(repo / ".cursor/rules/python.mdc", "cursor-private-rule\n")
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
    _write(repo / ".gitignore", "ignored/\n")
    _write(repo / "ignored/AGENTS.md", "must-not-be-read\n")
    _commit_all(repo)

    result = api.discover_project_instructions(repo)

    assert {item.relative_path for item in result.sources} == {
        ".cursor/rules/python.mdc",
        ".giga/harness.toml",
        ".giga/prompts/plan.md",
        "AGENTS.md",
        "GEMINI.md",
        "src/feature/AGENTS.md",
    }
    nested = next(
        item for item in result.sources if item.relative_path == "src/feature/AGENTS.md"
    )
    assert nested.scope_path == "src/feature"
    assert nested.materialization_owner == "agent_adapter"
    prompt = next(
        item for item in result.sources if item.relative_path == ".giga/prompts/plan.md"
    )
    assert prompt.kind is api.ProjectInstructionKind.PROJECT_PROMPT
    assert prompt.materialization_owner == "gigaloom_projects"
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert result.content_free is True
    assert "ignored/AGENTS.md" not in serialized
    assert "private-instruction" not in serialized
    assert "private-prompt" not in serialized
    assert "must-not-be-read" not in serialized


def test_rejects_symlinks_and_applies_file_and_source_bounds(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    outside = tmp_path / "private-home-rule.md"
    outside.write_text("outside-private-rule", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to(outside)
    _write(repo / "a/AGENTS.md", "a" * 32)
    _write(repo / "b/AGENTS.md", "b")
    _commit_all(repo)

    result = api.discover_project_instructions(
        repo,
        max_sources=2,
        max_file_bytes=8,
    )

    assert result.sources == ()
    assert {(item.relative_path, item.reason.value) for item in result.omissions} == {
        ("AGENTS.md", "symlink"),
        ("a/AGENTS.md", "file_too_large"),
        ("b/AGENTS.md", "limit"),
    }
    assert "outside-private-rule" not in json.dumps(result.to_dict())


def test_git_path_limit_and_missing_explicit_prompt_are_visible(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(
        repo / ".giga/harness.toml",
        '[presets.plan]\ntitle = "Plan"\nprompt_file = ".giga/prompts/missing.md"\n',
    )
    _write(repo / "a.txt", "a")
    _write(repo / "z/AGENTS.md", "late")
    _commit_all(repo)

    result = api.discover_project_instructions(repo, max_git_paths=2)

    assert result.scanned_paths_truncated is True
    assert any(
        item.selector_id == "discovery.git_visible_paths"
        and item.reason is api.InstructionDiscoveryOmissionReason.LIMIT
        for item in result.omissions
    )

    complete = api.discover_project_instructions(repo)
    assert any(
        item.relative_path == ".giga/prompts/missing.md"
        and item.reason is api.InstructionDiscoveryOmissionReason.NOT_GIT_VISIBLE
        for item in complete.omissions
    )


def test_discovery_is_deterministic_and_requires_exact_git_root(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    _write(repo / "AGENTS.md", "first\n")
    _commit_all(repo)

    first = api.discover_project_instructions(repo)
    same = api.discover_project_instructions(repo)
    _write(repo / "AGENTS.md", "second\n")
    changed = api.discover_project_instructions(repo)

    assert first == same
    assert first.source_revision == changed.source_revision
    assert first.discovery_digest != changed.discovery_digest
    with pytest.raises(ValueError, match="exact Git worktree root"):
        api.discover_project_instructions(repo / "src")


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
            "user.name=Instruction Discovery Fixture",
            "-c",
            "user.email=instructions@example.invalid",
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
