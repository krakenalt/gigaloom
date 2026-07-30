"""Bounded tmux capability and command execution primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Protocol


MAX_TMUX_OUTPUT_BYTES = 4096
DEFAULT_TMUX_COMMAND_TIMEOUT_SECONDS = 5.0
_TMUX_VERSION_PATTERN = re.compile(r"tmux ([0-9]+(?:\.[0-9]+)[0-9A-Za-z.-]*)\n?")


class TmuxCapabilityStatus(str, Enum):
    """Truthful managed-terminal capability state."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"


@dataclass(frozen=True)
class TmuxCapability:
    """Content-free evidence for one local tmux executable."""

    status: TmuxCapabilityStatus
    executable_path: str | None
    version: str | None
    reason: str

    @property
    def available(self) -> bool:
        """Return whether managed tmux operations may be attempted."""
        return self.status is TmuxCapabilityStatus.AVAILABLE


@dataclass(frozen=True)
class TmuxCommandResult:
    """Bounded result from one tmux client command."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class TmuxCommandTimeoutError(TimeoutError):
    """Raised when a tmux client command exceeds its bounded timeout."""


class TmuxCommandRunner(Protocol):
    """Execute tmux client commands without shell interpolation."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TMUX_COMMAND_TIMEOUT_SECONDS,
    ) -> TmuxCommandResult:
        """Run one command and return bounded output."""


class SubprocessTmuxCommandRunner:
    """Run tmux through argv-only subprocess calls."""

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_TMUX_COMMAND_TIMEOUT_SECONDS,
    ) -> TmuxCommandResult:
        """Run one tmux command with no stdin and bounded retained output."""
        try:
            completed = subprocess.run(
                tuple(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise TmuxCommandTimeoutError("tmux command timed out") from exc
        return TmuxCommandResult(
            returncode=completed.returncode,
            stdout=_bounded_bytes(completed.stdout),
            stderr=_bounded_bytes(completed.stderr),
        )


def probe_tmux(
    executable: str = "tmux",
    *,
    runner: TmuxCommandRunner | None = None,
    resolve_executable: Callable[[str], str | None] = shutil.which,
    platform: str | None = None,
) -> TmuxCapability:
    """Probe exact local tmux identity without touching a tmux server."""
    effective_platform = platform or sys.platform
    if effective_platform.startswith("win"):
        return TmuxCapability(
            status=TmuxCapabilityStatus.UNSUPPORTED,
            executable_path=None,
            version=None,
            reason="managed_tmux_is_unsupported_on_windows",
        )
    resolved = resolve_executable(executable)
    if resolved is None:
        return TmuxCapability(
            status=TmuxCapabilityStatus.MISSING,
            executable_path=None,
            version=None,
            reason="tmux_executable_not_found",
        )
    path = str(Path(resolved).expanduser().resolve())
    command_runner = runner or SubprocessTmuxCommandRunner()
    try:
        result = command_runner.run((path, "-V"), timeout_seconds=2.0)
    except (OSError, TmuxCommandTimeoutError):
        return TmuxCapability(
            status=TmuxCapabilityStatus.INVALID,
            executable_path=path,
            version=None,
            reason="tmux_version_probe_failed",
        )
    if result.returncode != 0:
        return TmuxCapability(
            status=TmuxCapabilityStatus.INVALID,
            executable_path=path,
            version=None,
            reason="tmux_version_probe_failed",
        )
    try:
        text = result.stdout.decode("ascii")
    except UnicodeDecodeError:
        text = ""
    match = _TMUX_VERSION_PATTERN.fullmatch(text)
    if match is None:
        return TmuxCapability(
            status=TmuxCapabilityStatus.INVALID,
            executable_path=path,
            version=None,
            reason="tmux_version_output_is_invalid",
        )
    return TmuxCapability(
        status=TmuxCapabilityStatus.AVAILABLE,
        executable_path=path,
        version=match.group(1),
        reason="tmux_is_available",
    )


def tmux_argv(
    capability: TmuxCapability,
    socket_path: Path,
    *commands: str,
    no_start: bool = False,
) -> tuple[str, ...]:
    """Build an argv-only command against one exact private socket."""
    if not capability.available or capability.executable_path is None:
        raise ValueError("tmux capability is unavailable")
    argv = [
        capability.executable_path,
        "-S",
        str(socket_path),
        "-f",
        "/dev/null",
    ]
    if no_start:
        argv.append("-N")
    argv.extend(commands)
    return tuple(argv)


def _bounded_bytes(value: bytes) -> bytes:
    if len(value) <= MAX_TMUX_OUTPUT_BYTES:
        return value
    return value[:MAX_TMUX_OUTPUT_BYTES]
