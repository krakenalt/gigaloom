"""Content-free console facts for native-agent launch planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
import sys
from typing import Any


@dataclass(frozen=True)
class TerminalContext:
    """Routing-relevant stream, platform, and managed-terminal facts."""

    stdin_is_tty: bool
    stdout_is_tty: bool
    stderr_is_tty: bool
    term: str | None
    ci: bool = False
    terminal_supported: bool = True
    platform: str = sys.platform

    @classmethod
    def capture(
        cls,
        *,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        environ: Mapping[str, str] | None = None,
        terminal_supported: bool = True,
        platform: str | None = None,
    ) -> TerminalContext:
        """Capture only facts needed to choose direct or managed native mode."""
        environment = os.environ if environ is None else environ
        input_stream = sys.stdin if stdin is None else stdin
        output_stream = sys.stdout if stdout is None else stdout
        error_stream = sys.stderr if stderr is None else stderr
        return cls(
            stdin_is_tty=bool(input_stream.isatty()),
            stdout_is_tty=bool(output_stream.isatty()),
            stderr_is_tty=bool(error_stream.isatty()),
            term=environment.get("TERM"),
            ci=_environment_truthy(environment.get("CI")),
            terminal_supported=terminal_supported,
            platform=sys.platform if platform is None else platform,
        )

    @property
    def fully_interactive(self) -> bool:
        """Return whether all standard streams belong to a human terminal."""
        return self.stdin_is_tty and self.stdout_is_tty and self.stderr_is_tty


def _environment_truthy(value: str | None) -> bool:
    return value is not None and value.strip().casefold() not in {
        "",
        "0",
        "false",
        "no",
    }
