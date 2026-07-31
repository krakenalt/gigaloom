"""Central native-agent gateway composition regression coverage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gigaloom.cli_commands.parser import build_parser
from gigaloom.cli_commands.registry import resolve_handler
from gigaloom.completion import root_completion_candidates
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.api import load_builtin_agent_profiles
from gigaloom.projects.api import (
    FilesystemProjectCatalogRepository,
    ProjectCatalogService,
)
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.app import create_app


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def test_root_registry_completion_and_run_guard_share_composed_commands(
    tmp_path: Path,
    capsys,
) -> None:
    parser = build_parser()
    root = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert {"project", "route", "capsule", "run"} <= set(root.choices)
    assert set(root_completion_candidates()) == {
        *root.choices,
        *(profile.agent_id for profile in load_builtin_agent_profiles()),
    }

    run_args = parser.parse_args(
        [
            "run",
            "--route-receipt",
            "route_fixture",
            "--route-confirmation",
            "confirm_fixture",
            "read only",
        ]
    )
    code = resolve_handler(run_args.handler)(
        run_args,
        HarnessConfig(data_dir=str(tmp_path / "state")),
    )
    assert code == 2
    assert "no run was started" in capsys.readouterr().err


def test_local_route_source_persists_honest_no_execution_receipt(
    tmp_path: Path,
    capsys,
) -> None:
    config = HarnessConfig(data_dir=str(tmp_path / "state"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = FilesystemProjectCatalogRepository(
        Path(config.data_dir) / "projects" / "catalog"
    )
    project = ProjectCatalogService(repository).add_project(
        workspace,
        display_name="composition fixture",
    )
    args = build_parser().parse_args(
        [
            "route",
            "recommend",
            "--project",
            project.catalog_project_id,
            "--intent",
            "read",
            "--task-digest",
            DIGEST_A,
            "--context-manifest-digest",
            DIGEST_B,
            "--capability",
            "structured_prompt",
            "--transport",
            "acp_stdio_v1",
            "--json",
        ]
    )

    assert resolve_handler(args.handler)(args, config) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["outcome"] in {"needs_human", "no_eligible_route"}
    assert output["eligible_routes"] == []
    assert tuple((Path(config.data_dir) / "route-decisions").glob("route_*.json"))


def test_fastapi_graph_mounts_all_wave_b_route_families(tmp_path: Path) -> None:
    app = create_app(
        HarnessConfig(data_dir=str(tmp_path / "state")),
        store=InMemoryHarnessSessionStore(),
    )
    paths = set(app.openapi()["paths"])

    assert {
        "/api/project-catalog",
        "/api/route-decisions/recommend",
        "/api/mcp-apps/frames",
        "/api/operator/runs/{run_id}/capsule",
    } <= paths
