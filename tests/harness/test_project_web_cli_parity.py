from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.cli_commands.commands.projects import (
    register_project_catalog_commands,
)
from gigaloom.cli_commands.handlers.project_launch import _handle_project_launch
from gigaloom.config import HarnessConfig
from gigaloom.projects.api import LaunchResolutionContextV1
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.routers.project_catalog import create_router
from gigaloom.ui.services.project_catalog import ProjectCatalogWebService


def _arguments(project_id: str, profile_id: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="giga")
    root = parser.add_subparsers(dest="command")
    project = root.add_parser("project")
    register_project_catalog_commands(project.add_subparsers(dest="project_command"))
    return parser.parse_args(
        [
            "project",
            "launch",
            project_id,
            "--profile",
            profile_id,
            "--dry-run",
            "--json",
        ]
    )


def _fake_executable(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize(
    ("agent_hint", "expected_agent_id", "expected_code"),
    [("codex", "codex", 0), ("missing-agent", None, 2)],
)
def test_project_launch_web_cli_resolution_parity(
    tmp_path,
    monkeypatch,
    capsys,
    agent_hint,
    expected_agent_id,
    expected_code,
):
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sessions = InMemoryHarnessSessionStore()
    web_service = ProjectCatalogWebService.from_data_dir(
        config.data_dir,
        session_store=sessions,
        launch_context=LaunchResolutionContextV1(
            agent_ids=frozenset({"codex"}),
            terminal_modes=frozenset({"direct"}),
        ),
    )
    app = FastAPI()
    app.include_router(create_router(web_service))
    client = TestClient(app)

    project = client.post(
        "/api/project-catalog",
        json={"path": str(workspace), "display_name": "Parity"},
    ).json()
    profile = client.post(
        f"/api/project-catalog/{project['catalog_project_id']}/launch-profiles",
        json={
            "display_name": "Native",
            "agent_hint": agent_hint,
            "model_hint": "future-model",
            "terminal_mode_hint": "direct",
        },
    ).json()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_executable(bin_dir / "codex")
    monkeypatch.setenv("PATH", str(bin_dir))

    code = _handle_project_launch(
        _arguments(project["catalog_project_id"], profile["launch_profile_id"]),
        config,
    )
    cli = json.loads(capsys.readouterr().out)
    detail = client.get(f"/api/project-catalog/{project['catalog_project_id']}").json()
    web = detail["launch_resolutions"][0]

    assert code == expected_code
    assert cli["agent_id"] == web["agent_id"] == expected_agent_id
    assert cli["authority_granted"] is web["authority_granted"] is False
    assert cli["unsatisfied_hints"] == web["unsatisfied_hints"]
    assert cli["provider_arguments"] == []
    assert "provider_arguments" not in web
