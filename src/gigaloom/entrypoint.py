"""Import-light console entry point for native agents and plain CLI routes."""

from __future__ import annotations

from collections.abc import Mapping
import sys
from typing import TYPE_CHECKING, Any

from gigaloom.cli_commands.metadata import run_metadata_command


if TYPE_CHECKING:
    from gigaloom.harnesses.agent_profiles import AgentProfileRegistry
    from gigaloom.native.api import TerminalContext


def main(
    argv: list[str] | None = None,
    *,
    context: TerminalContext | None = None,
    registry: AgentProfileRegistry | None = None,
) -> int:
    """Resolve root metadata, core commands, then declarative native agents."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    metadata_result = run_metadata_command(arguments)
    if metadata_result is not None:
        return metadata_result
    if not arguments:
        from gigaloom.cli_commands.launcher import render_launcher_summary
        from gigaloom.harnesses.agent_profiles import load_builtin_agent_profiles

        profiles = (
            registry.profiles if registry is not None else load_builtin_agent_profiles()
        )
        print(
            render_launcher_summary(
                profiles,
                facade_executable=sys.argv[0],
                platform=(context.platform if context is not None else None),
            ),
            end="",
        )
        return 0
    gateway_result = _run_gateway_selector(
        arguments,
        context=context,
        registry=registry,
    )
    if gateway_result is not None:
        return gateway_result
    if arguments[0].startswith("-"):
        return _run_core_command(arguments)

    from gigaloom.native.api import AgentResolutionKind, TerminalContext

    profiles = registry or _default_registry()
    resolution = profiles.resolve(arguments[0])
    if resolution.kind is AgentResolutionKind.CORE_COMMAND:
        return _run_core_command(arguments)
    if resolution.kind in {
        AgentResolutionKind.AGENT_ID,
        AgentResolutionKind.AGENT_ALIAS,
    }:
        result = run_native_namespace(
            arguments,
            registry=profiles,
            facade_executable=sys.argv[0],
            context=context or TerminalContext.capture(),
        )
        assert result is not None
        return result
    print(
        f"giga: unknown command or agent {arguments[0]!r}",
        file=sys.stderr,
    )
    if resolution.suggestions:
        print(
            "Did you mean: " + ", ".join(resolution.suggestions),
            file=sys.stderr,
        )
    return 2


def _default_registry() -> AgentProfileRegistry:
    from gigaloom.harnesses.agent_profiles import (
        AgentProfileRegistry,
        build_core_command_collision_contract,
        load_builtin_agent_profiles,
    )

    return AgentProfileRegistry.build(
        load_builtin_agent_profiles(),
        collision_contract=build_core_command_collision_contract(
            _registered_core_commands()
        ),
    )


def _registered_core_commands() -> tuple[str, ...]:
    from gigaloom.cli_commands.parser import build_parser

    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    choices = command_action.choices
    if choices is None:
        raise RuntimeError("root CLI parser has no command registry")
    return tuple(choices)


def _run_core_command(arguments: list[str]) -> int:
    from gigaloom.cli_commands.main import main as cli_main

    return cli_main(arguments)


def _run_gateway_selector(
    arguments: list[str],
    *,
    context: TerminalContext | None,
    registry: AgentProfileRegistry | None,
) -> int | None:
    if not _has_gateway_selector(arguments):
        return None
    from gigaloom.cli_commands.gateway_launch import (
        GatewayLaunchParseError,
        parse_gateway_launch_argv,
    )

    try:
        request = parse_gateway_launch_argv(arguments)
    except GatewayLaunchParseError as error:
        print(f"giga: gateway launch: {error.code.value}", file=sys.stderr)
        return 2
    if request is None:
        return None

    from gigaloom.config import HarnessConfig
    from gigaloom.native.api import TerminalContext
    from gigaloom.cli_commands.gateway_application import (
        build_gateway_launch_application,
    )
    from gigaloom.native_cli_process import run_native_l1_handoff

    profiles = registry or _default_registry()
    terminal = context or TerminalContext.capture()

    def launch(argv: tuple[str, ...], environment: Mapping[str, str]) -> int:
        result = run_native_namespace(
            argv,
            registry=profiles,
            environment=environment,
            facade_executable=sys.argv[0],
            runner=run_native_l1_handoff,
            managed_runner=run_native_l1_handoff,
            context=terminal,
        )
        return 2 if result is None else result

    return build_gateway_launch_application(HarnessConfig.from_env()).run(
        request,
        native_launcher=launch,
    )


def _has_gateway_selector(arguments: list[str]) -> bool:
    selectors = {"--route", "--with", "--model"}
    for argument in arguments:
        if not argument.startswith("-"):
            return False
        if argument.partition("=")[0] in selectors:
            return True
    return False


def run_native_namespace(*args: Any, **kwargs: Any) -> Any:
    """Load the native facade only when an agent route is selected."""
    from gigaloom.native_cli_facade import run_native_namespace as run

    return run(*args, **kwargs)
