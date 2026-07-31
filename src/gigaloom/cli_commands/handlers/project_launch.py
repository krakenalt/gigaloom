"""Explicit Project Launch Profile to native-agent CLI orchestration."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Iterator

from gigaloom.cli_commands.parser import build_parser
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistrySnapshot,
    build_core_command_collision_contract,
    load_agent_profile_registry,
)
from gigaloom.native.api import (
    NativeInvocation,
    NativeLaunchMode,
    TerminalContext,
    plan_native_launch,
)
from gigaloom.native_cli_facade import run_native_namespace
from gigaloom.projects.api import (
    FilesystemLaunchProfileRepository,
    FilesystemProjectCatalogRepository,
    LaunchResolutionContextV1,
    ProjectCatalogConflictError,
    ProjectLaunchProfileService,
    ProjectLaunchProfileV1,
)


def _handle_project_launch(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Preview and explicitly launch one profile-selected native agent."""
    if args.json and not args.dry_run:
        raise ValueError("--json requires --dry-run for project launch")
    terminal = TerminalContext.capture()
    snapshot = _agent_snapshot(config)
    project, profile_service, profile = _project_launch_inputs(
        config,
        args.catalog_project_id,
        args.launch_profile_id,
    )
    if project.state != "active" or project.location.canonical_path is None:
        raise ProjectCatalogConflictError(
            "project launch requires an active resolved project location"
        )
    resolution = profile_service.resolve_profile(
        profile.launch_profile_id,
        _resolution_context(snapshot, terminal),
    )
    payload = _launch_preview(
        project=project,
        profile=profile,
        resolution=resolution,
        snapshot=snapshot,
        terminal=terminal,
    )
    _render_preview(payload, as_json=args.json)
    if args.dry_run:
        return 0 if payload["ready"] else 2
    if not payload["ready"]:
        return 2

    agent_id = payload["agent_id"]
    assert isinstance(agent_id, str)
    terminal_mode = payload["terminal_mode"]
    managed_supported = None if terminal_mode == "managed" else False
    with _working_directory(project.location.canonical_path):
        result = run_native_namespace(
            (agent_id,),
            registry=snapshot.registry,
            facade_executable=sys.argv[0],
            context=terminal,
            managed_terminal_supported=managed_supported,
        )
    if result is None:
        raise RuntimeError("resolved project agent was not launchable")
    return result


def _project_launch_inputs(
    config: HarnessConfig,
    catalog_project_id: str,
    launch_profile_id: str | None,
):
    root = Path(config.data_dir) / "projects"
    catalog_repository = FilesystemProjectCatalogRepository(root / "catalog")
    profile_repository = FilesystemLaunchProfileRepository(root / "launch_profiles")
    project = catalog_repository.get(catalog_project_id)
    profile_service = ProjectLaunchProfileService(
        profile_repository,
        catalog_repository,
    )
    if launch_profile_id is None:
        page = profile_repository.list_page(catalog_project_id, limit=2)
        if len(page.items) != 1 or page.has_more:
            raise ValueError(
                "project launch requires --profile unless exactly one profile exists"
            )
        profile = page.items[0]
    else:
        profile = profile_repository.get(launch_profile_id)
        if profile.catalog_project_id != project.catalog_project_id:
            raise ProjectCatalogConflictError(
                "launch profile belongs to another project"
            )
    return project, profile_service, profile


def _agent_snapshot(config: HarnessConfig) -> AgentProfileRegistrySnapshot:
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    choices = command_action.choices
    if choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    return load_agent_profile_registry(
        config.data_dir,
        collision_contract=build_core_command_collision_contract(choices),
    )


def _resolution_context(
    snapshot: AgentProfileRegistrySnapshot,
    terminal: TerminalContext,
) -> LaunchResolutionContextV1:
    agent_ids = frozenset(
        profile.agent_id
        for profile in snapshot.registry.profiles
        if profile.native is not None and terminal.platform in profile.platform_support
    )
    route_ids = frozenset(
        route.route_id
        for profile in snapshot.registry.profiles
        for route in profile.structured_routes
    )
    terminal_modes = {"direct"}
    if terminal.fully_interactive and terminal.terminal_supported:
        terminal_modes.add("managed")
    return LaunchResolutionContextV1(
        agent_ids=agent_ids,
        structured_route_ids=route_ids,
        terminal_modes=frozenset(terminal_modes),
    )


def _launch_preview(
    *,
    project,
    profile: ProjectLaunchProfileV1,
    resolution,
    snapshot: AgentProfileRegistrySnapshot,
    terminal: TerminalContext,
) -> dict[str, object]:
    agent_id = resolution.agent_id
    executable_status = "unresolved"
    terminal_mode: str | None = None
    blocking_hints = {
        item.field
        for item in resolution.unsatisfied_hints
        if item.field in {"agent_hint", "terminal_mode_hint"}
    }
    if agent_id is not None:
        agent = snapshot.registry.get(agent_id)
        native = agent.native
        if native is None:
            executable_status = "no_native_route"
        else:
            executable_status = (
                "ready"
                if any(
                    shutil.which(name) is not None for name in native.executable_names
                )
                else "missing"
            )
            invocation = NativeInvocation(
                agent_id=agent_id,
                requested_token=agent_id,
                suffix=(),
                cwd=project.location.canonical_path,
                stdin_is_tty=terminal.stdin_is_tty,
                stdout_is_tty=terminal.stdout_is_tty,
                stderr_is_tty=terminal.stderr_is_tty,
                ci=terminal.ci,
                platform=terminal.platform,
            )
            launch_plan = plan_native_launch(
                invocation,
                native,
                managed_terminal_supported=(
                    False
                    if resolution.terminal_mode == "direct"
                    else terminal.terminal_supported
                ),
            )
            terminal_mode = (
                "managed"
                if launch_plan.mode is NativeLaunchMode.MANAGED_NATIVE
                else "direct"
            )
            if (
                resolution.terminal_mode in {"direct", "managed"}
                and terminal_mode != resolution.terminal_mode
            ):
                blocking_hints.add("terminal_mode_hint")
    if agent_id is None and profile.agent_hint is None:
        blocking_hints.add("agent_hint")
    ready = agent_id is not None and executable_status == "ready" and not blocking_hints
    return {
        "schema_version": 1,
        "ready": ready,
        "catalog_project_id": project.catalog_project_id,
        "project_digest": project.digest,
        "launch_profile_id": profile.launch_profile_id,
        "launch_profile_digest": profile.digest,
        "cwd": project.location.canonical_path,
        "agent_id": agent_id,
        "terminal_mode": terminal_mode,
        "executable_status": executable_status,
        "provider_arguments": [],
        "authority_granted": False,
        "unsatisfied_hints": [asdict(item) for item in resolution.unsatisfied_hints],
        "blocking_hints": sorted(blocking_hints),
        "profile_issues": [asdict(item) for item in snapshot.issues],
    }


def _render_preview(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        return
    print("Project native launch plan", flush=True)
    print(f"  project: {payload['catalog_project_id']}", flush=True)
    print(f"  cwd: {payload['cwd']}", flush=True)
    print(f"  agent: {payload['agent_id'] or 'unsatisfied'}", flush=True)
    print(f"  terminal mode: {payload['terminal_mode'] or 'unsatisfied'}", flush=True)
    print(f"  executable: {payload['executable_status']}", flush=True)
    hints = payload["unsatisfied_hints"]
    assert isinstance(hints, list)
    for hint in hints:
        assert isinstance(hint, dict)
        print(
            f"  unsatisfied: {hint['field']}={hint['value']}",
            flush=True,
        )
    print("  provider arguments: none", flush=True)


@contextmanager
def _working_directory(path: str) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


__all__ = ["_handle_project_launch"]
