"""Import-light dispatch for CLI metadata commands."""

from __future__ import annotations

from collections.abc import Sequence


_NATIVE_NAMESPACES = frozenset({"claude", "codex", "gemini"})


def run_metadata_command(argv: Sequence[str]) -> int | None:
    """Run one metadata-only command or return control to normal dispatch."""
    arguments = tuple(argv)
    command_path = _command_path(arguments)
    if command_path and command_path[0] in _NATIVE_NAMESPACES:
        return None

    if arguments in {("--help",), ("-h",)}:
        from gpt2giga_harness.tui.entrypoint import build_parser

        build_parser().parse_args(list(arguments))
        raise AssertionError("argparse help action did not exit")

    if arguments == ("--version",):
        from gpt2giga_harness import __version__

        print(f"GigaLoom {__version__} (gigaloom)")
        return 0

    if _is_automation_metadata(arguments, command_path):
        from gpt2giga_harness.cli_commands.parser import build_parser

        args = build_parser().parse_args(list(arguments))
        if args.handler == "_handle_completion":
            from gpt2giga_harness.completion import render_completion

            print(render_completion(args.shell), end="")
            return 0
        if args.handler == "_handle_config_path":
            from gpt2giga_harness.executables import user_config_path

            print(user_config_path())
            return 0
        raise AssertionError(f"unhandled metadata command: {args.handler}")

    return None


def _is_automation_metadata(
    arguments: tuple[str, ...],
    command_path: tuple[str, ...],
) -> bool:
    if "--non-interactive" in arguments and (
        "--help" in arguments or "-h" in arguments or "--version" in arguments
    ):
        return True
    if command_path[:1] == ("completion",):
        return True
    return command_path[:2] == ("config", "path")


def _command_path(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(argument for argument in arguments if not argument.startswith("-"))
