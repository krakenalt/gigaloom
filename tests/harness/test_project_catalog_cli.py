from __future__ import annotations

import argparse
import json

from gigaloom.cli_commands.commands.projects import (
    register_project_catalog_commands,
)
from gigaloom.cli_commands.handlers.projects import (
    resolve_project_command_handler,
)
from gigaloom.config import HarnessConfig
from gigaloom.sessions import FilesystemHarnessSessionStore


def _run(arguments, *, config, capsys):
    parser = argparse.ArgumentParser(prog="giga")
    root = parser.add_subparsers(dest="command")
    project = root.add_parser("project")
    register_project_catalog_commands(project.add_subparsers(dest="project_command"))
    args = parser.parse_args(["project", *arguments])
    code = resolve_project_command_handler(args.handler)(args, config)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def test_project_catalog_cli_lifecycle_and_dry_run_are_content_safe(
    tmp_path,
    capsys,
):
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("user data\n", encoding="utf-8")

    preview_code, preview = _run(
        ["add", str(workspace), "--name", "Demo", "--dry-run", "--json"],
        config=config,
        capsys=capsys,
    )
    list_code, empty = _run(["list", "--json"], config=config, capsys=capsys)
    add_code, added = _run(
        ["add", str(workspace), "--name", "Demo", "--json"],
        config=config,
        capsys=capsys,
    )
    project_id = added["project"]["catalog_project_id"]
    rename_code, renamed = _run(
        ["rename", project_id, "Renamed", "--json"],
        config=config,
        capsys=capsys,
    )
    remove_code, removed = _run(
        ["remove", project_id, "--json"],
        config=config,
        capsys=capsys,
    )

    assert preview_code == list_code == add_code == rename_code == remove_code == 0
    assert preview["dry_run"] is True
    assert empty["projects"] == []
    assert renamed["project"]["display_name"] == "Renamed"
    assert removed["project"]["state"] == "tombstoned"
    assert removed["repository_data_deleted"] is False
    assert removed["session_data_deleted"] is False
    assert marker.read_text(encoding="utf-8") == "user data\n"


def test_project_profile_cli_crud_preserves_soft_hints(tmp_path, capsys):
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _, added = _run(
        ["add", str(workspace), "--name", "Demo", "--json"],
        config=config,
        capsys=capsys,
    )
    project_id = added["project"]["catalog_project_id"]

    preview_code, preview = _run(
        [
            "profile",
            "create",
            project_id,
            "--name",
            "Review",
            "--agent-hint",
            "codex",
            "--model-hint",
            "gpt-next",
            "--terminal-mode",
            "direct",
            "--dry-run",
            "--json",
        ],
        config=config,
        capsys=capsys,
    )
    list_code, empty = _run(
        ["profile", "list", project_id, "--json"],
        config=config,
        capsys=capsys,
    )
    create_code, created = _run(
        [
            "profile",
            "create",
            project_id,
            "--name",
            "Review",
            "--agent-hint",
            "codex",
            "--model-hint",
            "gpt-next",
            "--terminal-mode",
            "direct",
            "--json",
        ],
        config=config,
        capsys=capsys,
    )
    profile_id = created["launch_profile"]["launch_profile_id"]
    update_code, updated = _run(
        [
            "profile",
            "update",
            profile_id,
            "--name",
            "Direct review",
            "--clear-model-hint",
            "--json",
        ],
        config=config,
        capsys=capsys,
    )
    delete_code, deleted = _run(
        ["profile", "delete", profile_id, "--json"],
        config=config,
        capsys=capsys,
    )

    assert preview_code == list_code == create_code == update_code == delete_code == 0
    assert preview["dry_run"] is True
    assert empty["launch_profiles"] == []
    assert updated["launch_profile"]["display_name"] == "Direct review"
    assert updated["launch_profile"]["model_hint"] is None
    assert deleted["launch_profile"]["agent_hint"] == "codex"
    assert "credentials" not in json.dumps(created)
    assert "environment" not in created["launch_profile"]


def test_project_move_session_cli_supports_catalog_and_unfiled(tmp_path, capsys):
    config = HarnessConfig(data_dir=str(tmp_path / "data"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    _, added = _run(
        ["add", str(workspace), "--name", "Demo", "--json"],
        config=config,
        capsys=capsys,
    )
    project_id = added["project"]["catalog_project_id"]
    store = FilesystemHarnessSessionStore(config.data_dir)
    session = store.create_session(
        title="Move me",
        metadata={"project_id": "legacy"},
    )

    preview_code, preview = _run(
        ["move-session", session.id, "--to", project_id, "--dry-run", "--json"],
        config=config,
        capsys=capsys,
    )
    move_code, moved = _run(
        ["move-session", session.id, "--to", project_id, "--json"],
        config=config,
        capsys=capsys,
    )
    unfile_code, unfiled = _run(
        ["move-session", session.id, "--to", "unfiled", "--json"],
        config=config,
        capsys=capsys,
    )

    assert preview_code == move_code == unfile_code == 0
    assert preview["to_catalog_project_id"] == project_id
    assert moved["session"]["catalog_project_id"] == project_id
    assert unfiled["session"]["catalog_project_id"] is None
    assert "project_id" not in store.get_session(session.id).metadata
