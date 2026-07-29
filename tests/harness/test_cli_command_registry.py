from __future__ import annotations

import subprocess
import sys
import textwrap


def _run_isolated_script(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(source)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_parser_metadata_does_not_import_command_handlers():
    result = _run_isolated_script(
        """
        import sys

        from gpt2giga_harness.cli_commands.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["ui"])
        assert args.handler == "_handle_ui"
        assert not any(
            name.startswith("gpt2giga_harness.cli_commands.handlers")
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_registry_imports_only_the_selected_handler_group():
    result = _run_isolated_script(
        """
        import sys

        from gpt2giga_harness.cli_commands.registry import resolve_handler

        handler = resolve_handler("_handle_provider_list")
        assert handler.__module__ == (
            "gpt2giga_harness.cli_commands.handlers.provider"
        )
        assert "gpt2giga_harness.cli_commands.handlers.provider" in sys.modules
        assert "gpt2giga_harness.cli_commands.handlers.ui" not in sys.modules
        assert "gpt2giga_harness.cli_commands.handlers.worker" not in sys.modules
        """
    )

    assert result.returncode == 0, result.stderr


def test_every_registered_handler_resolves():
    import argparse

    from gpt2giga_harness.cli_commands.parser import build_parser
    from gpt2giga_harness.cli_commands.registry import resolve_handler

    pending = [build_parser()]
    handler_names: set[str] = set()
    while pending:
        parser = pending.pop()
        handler = parser.get_default("handler")
        if handler is not None:
            handler_names.add(handler)
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                pending.extend(action.choices.values())

    assert len(handler_names) >= 70
    assert all(callable(resolve_handler(name)) for name in handler_names)
