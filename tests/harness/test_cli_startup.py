"""Measured import boundaries for the root CLI startup path."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from gigaloom.cli_commands.core_commands import CORE_COMMANDS
from gigaloom.cli_commands.parser import build_parser


ROOT = Path(__file__).resolve().parents[2]


def test_core_command_registry_matches_the_composed_parser() -> None:
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )

    assert tuple(command_action.choices or ()) == CORE_COMMANDS


def test_default_native_registry_does_not_import_the_root_parser() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.fspath(ROOT / "src")
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            "from gigaloom.entrypoint import _default_registry; "
            "_default_registry(); "
            "import sys; "
            "raise SystemExit(int('gigaloom.cli_commands.parser' in sys.modules))",
        ),
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr


def test_metadata_command_does_not_import_the_core_registry() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.fspath(ROOT / "src")
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            "from gigaloom.entrypoint import main; "
            "main(['--version']); "
            "import sys; "
            "raise SystemExit(int('gigaloom.cli_commands.core_commands' "
            "in sys.modules))",
        ),
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
