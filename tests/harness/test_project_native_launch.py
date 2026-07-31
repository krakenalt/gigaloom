from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from gigaloom.cli_commands.commands.projects import (
    register_project_catalog_commands,
)
from gigaloom.cli_commands.handlers.project_launch import _handle_project_launch
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    ProjectCatalogService,
    ProjectLaunchProfileService,
)


def _arguments(values):
    parser = argparse.ArgumentParser(prog="giga")
    root = parser.add_subparsers(dest="command")
    project = root.add_parser("project")
    register_project_catalog_commands(project.add_subparsers(dest="project_command"))
    return parser.parse_args(["project", *values])


def _project_profile(tmp_path, *, agent_hint="codex", model_hint="future-model"):
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    root = tmp_path / "repo"
    root.mkdir()
    state = Path(config.data_dir) / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(state / "catalog")
    project = ProjectCatalogService(catalog_repository).add_project(
        root,
        display_name="Demo",
    )
    profile_repository = FilesystemLaunchProfileRepository(state / "launch_profiles")
    profile = ProjectLaunchProfileService(
        profile_repository,
        catalog_repository,
    ).create_profile(
        project.catalog_project_id,
        display_name="Native",
        agent_hint=agent_hint,
        model_hint=model_hint,
        terminal_mode_hint="direct",
    )
    return config, root, project, profile


def _fake_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_project_launch_dry_run_previews_exact_cwd_and_unsatisfied_soft_hint(
    tmp_path,
    monkeypatch,
    capsys,
):
    config, root, project, profile = _project_profile(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir / "codex")
    monkeypatch.setenv("PATH", str(bin_dir))

    code = _handle_project_launch(
        _arguments(
            [
                "launch",
                project.catalog_project_id,
                "--profile",
                profile.launch_profile_id,
                "--dry-run",
                "--json",
            ]
        ),
        config,
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ready"] is True
    assert payload["cwd"] == str(root.resolve())
    assert payload["agent_id"] == "codex"
    assert payload["terminal_mode"] == "direct"
    assert payload["provider_arguments"] == []
    assert payload["authority_granted"] is False
    assert payload["unsatisfied_hints"] == [
        {"field": "model_hint", "reason": "unavailable", "value": "future-model"}
    ]


def test_project_launch_executes_generic_native_path_from_project_cwd(
    tmp_path,
    monkeypatch,
    capsys,
):
    config, root, project, profile = _project_profile(
        tmp_path,
        model_hint=None,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir / "codex")
    monkeypatch.setenv("PATH", str(bin_dir))
    calls = []

    def fake_launch(argv, **kwargs):
        calls.append(
            {
                "argv": argv,
                "cwd": os.getcwd(),
                "registry": kwargs["registry"],
                "managed": kwargs["managed_terminal_supported"],
            }
        )
        return 23

    monkeypatch.setattr(
        "gigaloom.cli_commands.handlers.project_launch.run_native_namespace",
        fake_launch,
    )

    code = _handle_project_launch(
        _arguments(
            [
                "launch",
                project.catalog_project_id,
                "--profile",
                profile.launch_profile_id,
            ]
        ),
        config,
    )

    assert code == 23
    assert calls[0]["argv"] == ("codex",)
    assert calls[0]["cwd"] == str(root.resolve())
    assert calls[0]["managed"] is False
    assert calls[0]["registry"].get("codex").agent_id == "codex"
    assert Path.cwd() != root.resolve()
    assert "provider arguments: none" in capsys.readouterr().out


def test_project_launch_never_substitutes_an_unsatisfied_agent(
    tmp_path,
    monkeypatch,
    capsys,
):
    config, _, project, profile = _project_profile(
        tmp_path,
        agent_hint="missing-agent",
        model_hint=None,
    )
    monkeypatch.setattr(
        "gigaloom.cli_commands.handlers.project_launch.run_native_namespace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsatisfied agent must not launch")
        ),
    )

    code = _handle_project_launch(
        _arguments(
            [
                "launch",
                project.catalog_project_id,
                "--profile",
                profile.launch_profile_id,
                "--dry-run",
                "--json",
            ]
        ),
        config,
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["ready"] is False
    assert payload["agent_id"] is None
    assert payload["blocking_hints"] == ["agent_hint"]
