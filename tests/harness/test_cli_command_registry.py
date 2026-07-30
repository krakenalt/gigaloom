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

        from gigaloom.cli_commands.parser import build_parser

        parser = build_parser()
        args = parser.parse_args(["ui"])
        assert args.handler == "_handle_ui"
        assert not any(
            name.startswith("gigaloom.cli_commands.handlers")
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_registry_imports_only_the_selected_handler_group():
    result = _run_isolated_script(
        """
        import sys

        from gigaloom.cli_commands.registry import resolve_handler

        handler = resolve_handler("_handle_provider_list")
        assert handler.__module__ == (
            "gigaloom.cli_commands.handlers.provider"
        )
        assert "gigaloom.cli_commands.handlers.provider" in sys.modules
        assert "gigaloom.cli_commands.handlers.ui" not in sys.modules
        assert "gigaloom.cli_commands.handlers.worker" not in sys.modules
        """
    )

    assert result.returncode == 0, result.stderr


def test_console_metadata_commands_do_not_import_domain_graph():
    result = _run_isolated_script(
        """
        import contextlib
        import io
        import sys

        from gigaloom.entrypoint import main

        cases = (
            (["--version"], False),
            (["--help"], True),
            (["--non-interactive", "--help"], True),
            (["completion", "bash"], False),
            (["config", "path"], False),
        )
        for arguments, exits in cases:
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    result = main(arguments)
                except SystemExit as exc:
                    assert exits
                    assert exc.code == 0
                else:
                    assert not exits
                    assert result == 0
            assert "gigaloom.cli" not in sys.modules
            assert not any(
                name.split(".", 1)[0] in {"fastapi", "textual", "uvicorn"}
                for name in sys.modules
            )
        """
    )

    assert result.returncode == 0, result.stderr


def test_console_provider_command_does_not_import_cli_ui_or_tui(tmp_path):
    result = _run_isolated_script(
        f"""
        import os
        import sys

        os.environ["GPT2GIGA_HARNESS_DATA_DIR"] = {str(tmp_path)!r}

        from gigaloom.entrypoint import main

        assert main(["provider", "list", "--json"]) == 0
        assert "gigaloom.cli" not in sys.modules
        assert not any(
            name.split(".", 1)[0] in {{"fastapi", "textual", "uvicorn"}}
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_console_harness_list_does_not_import_cli_ui_or_tui(tmp_path):
    result = _run_isolated_script(
        f"""
        import contextlib
        import io
        import os
        import sys

        os.environ["GPT2GIGA_HARNESS_DATA_DIR"] = {str(tmp_path)!r}

        from gigaloom.entrypoint import main

        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            assert main(["harness", "list", "--json"]) == 0
        assert '"id": "direct-chat"' in stdout.getvalue()
        assert "gigaloom.cli" not in sys.modules
        assert not any(
            name.split(".", 1)[0] in {{"fastapi", "textual", "uvicorn"}}
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_cli_error_mapping_is_import_light():
    result = _run_isolated_script(
        """
        import sys

        from gigaloom.cli_commands.errors import format_cli_error

        unknown_provider = type(
            "ProviderSettingsNotFoundError",
            (Exception,),
            {"__module__": "gigaloom.provider_settings"},
        )("fixture")
        derived_unknown_provider = type(
            "DerivedProviderSettingsNotFoundError",
            (type(unknown_provider),),
            {"__module__": "tests.fixture"},
        )("derived")
        provider_conflict = type(
            "ProviderRegistryConflict",
            (Exception,),
            {"__module__": "gigaloom.provider_registry"},
        )("revision")
        integration_error = type(
            "IntegrationFlowError",
            (Exception,),
            {"__module__": "gigaloom.integration_flows"},
        )("conflict")

        assert format_cli_error(unknown_provider) == "Unknown provider: fixture"
        assert format_cli_error(derived_unknown_provider) == (
            "Unknown provider: derived"
        )
        assert format_cli_error(provider_conflict) == (
            "Provider registry conflict: revision"
        )
        assert format_cli_error(integration_error) == "conflict"
        assert format_cli_error(ValueError("invalid")) == "invalid"
        assert format_cli_error(RuntimeError("unexpected")) is None
        assert not any(
            name.split(".", 1)[0] in {"fastapi", "textual", "uvicorn"}
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_all_registered_handlers_defer_ui_frameworks_until_execution():
    result = _run_isolated_script(
        """
        import argparse
        import sys

        from gigaloom.cli_commands.parser import build_parser
        from gigaloom.cli_commands.registry import resolve_handler

        pending = [build_parser()]
        handler_names = set()
        while pending:
            parser = pending.pop()
            handler = parser.get_default("handler")
            if handler is not None:
                handler_names.add(handler)
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    pending.extend(action.choices.values())

        assert len(handler_names) >= 89
        for handler_name in sorted(handler_names):
            assert callable(resolve_handler(handler_name))
        assert not any(
            name.split(".", 1)[0] in {"fastapi", "textual", "uvicorn"}
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_non_ui_domain_imports_defer_ui_frameworks():
    result = _run_isolated_script(
        """
        import sys

        import gigaloom.doctor
        import gigaloom.performance_baseline
        import gigaloom.tui.commands
        import gigaloom.ui.local_access

        assert not any(
            name.split(".", 1)[0] in {"fastapi", "textual", "uvicorn"}
            for name in sys.modules
        )
        """
    )

    assert result.returncode == 0, result.stderr


def test_representative_non_ui_commands_defer_ui_frameworks(tmp_path):
    result = _run_isolated_script(
        f"""
        import contextlib
        import io
        import os
        import sys

        os.environ["GPT2GIGA_HARNESS_DATA_DIR"] = {str(tmp_path)!r}

        from gigaloom.entrypoint import main

        cases = (
            ["doctor", "--json"],
            ["harness", "capabilities", "--json"],
            ["worker", "status", "--json"],
        )
        for arguments in cases:
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                assert main(arguments) == 0
            assert not any(
                name.split(".", 1)[0] in {{"fastapi", "textual", "uvicorn"}}
                for name in sys.modules
            )
        """
    )

    assert result.returncode == 0, result.stderr


def test_native_metadata_suffix_is_not_claimed_by_harness_fast_path():
    result = _run_isolated_script(
        """
        from gigaloom.cli_commands.metadata import run_metadata_command

        assert run_metadata_command(["codex", "--version"]) is None
        assert run_metadata_command(["claude", "--help"]) is None
        assert run_metadata_command(["gemini", "completion", "bash"]) is None
        """
    )

    assert result.returncode == 0, result.stderr


def test_every_registered_handler_resolves():
    import argparse

    from gigaloom.cli_commands.parser import build_parser
    from gigaloom.cli_commands.registry import resolve_handler

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
