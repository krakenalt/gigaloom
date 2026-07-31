"""Early root routing from declarative agent profiles to native CLIs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
import sys

from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistry,
    AgentProfileV1,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
)
from gigaloom.native.api import (
    AgentResolutionKind,
    NativeInvocation,
    NativeLaunchMode,
    TerminalContext,
    plan_native_launch,
)
from gigaloom.native_cli_process import (
    NativeProcessSpec,
    run_native_l0,
    run_native_l1_handoff,
)


NativeRunner = Callable[..., int]
ManagedRunner = Callable[..., int]


def default_agent_profile_registry(
    *,
    registered_commands: Sequence[str] = (),
) -> AgentProfileRegistry:
    """Load the bounded built-in profile snapshot without provider imports."""
    return AgentProfileRegistry.build(
        load_builtin_agent_profiles(),
        collision_contract=build_core_command_collision_contract(registered_commands),
    )


def match_native_namespace(
    argv: Sequence[str],
    *,
    registry: AgentProfileRegistry | None = None,
) -> tuple[AgentProfileV1, tuple[str, ...]] | None:
    """Return one registered agent profile and its untouched provider suffix."""
    if not argv:
        return None
    profiles = registry or default_agent_profile_registry()
    resolution = profiles.resolve(argv[0])
    if resolution.kind not in {
        AgentResolutionKind.AGENT_ID,
        AgentResolutionKind.AGENT_ALIAS,
    }:
        return None
    assert resolution.agent_id is not None
    return profiles.get(resolution.agent_id), tuple(argv[1:])


def run_native_namespace(
    argv: Sequence[str],
    *,
    registry: AgentProfileRegistry | None = None,
    environment: Mapping[str, str] | None = None,
    facade_executable: str | os.PathLike[str] | None = None,
    runner: NativeRunner = run_native_l0,
    managed_runner: ManagedRunner = run_native_l1_handoff,
    context: TerminalContext | None = None,
    managed_terminal_supported: bool | None = None,
) -> int | None:
    """Launch a registered native agent without consulting structured routes."""
    matched = match_native_namespace(argv, registry=registry)
    if matched is None:
        return None
    profile, suffix = matched
    native = profile.native
    if native is None:
        _write_no_native_diagnostic(profile.agent_id)
        return 126
    terminal = context or TerminalContext.capture()
    invocation = NativeInvocation(
        agent_id=profile.agent_id,
        requested_token=argv[0],
        suffix=suffix,
        cwd=os.getcwd(),
        stdin_is_tty=terminal.stdin_is_tty,
        stdout_is_tty=terminal.stdout_is_tty,
        stderr_is_tty=terminal.stderr_is_tty,
        ci=terminal.ci,
        platform=terminal.platform,
    )
    plan = plan_native_launch(
        invocation,
        native,
        managed_terminal_supported=(
            terminal.terminal_supported
            if managed_terminal_supported is None
            else managed_terminal_supported
        ),
    )
    process_spec = NativeProcessSpec(
        namespace=profile.agent_id,
        executable=native.executable_names[0],
    )
    selected_runner = (
        managed_runner if plan.mode is NativeLaunchMode.MANAGED_NATIVE else runner
    )
    return selected_runner(
        process_spec,
        suffix,
        environment=environment,
        facade_executable=facade_executable,
    )


def _write_no_native_diagnostic(agent_id: str) -> None:
    message = f"giga: agent {agent_id} has no native launch route\n"
    try:
        sys.stderr.write(message)
    except (AttributeError, OSError, ValueError):
        pass
