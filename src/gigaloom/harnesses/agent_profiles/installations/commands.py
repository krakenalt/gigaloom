"""Tokenized bounded subprocess boundary for private package environments."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import threading
from typing import Mapping, Protocol, runtime_checkable

from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError


DEFAULT_PACKAGE_COMMAND_TIMEOUT_SECONDS = 300.0
MAX_PACKAGE_COMMAND_OUTPUT_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class PackageCommand:
    """One argv-only command scoped to a private working directory."""

    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    timeout_seconds: float = DEFAULT_PACKAGE_COMMAND_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class PackageCommandResult:
    """Bounded process outcome; raw output is never persisted to receipts."""

    return_code: int
    stdout: bytes = b""
    stderr: bytes = b""


@runtime_checkable
class PackageCommandRunner(Protocol):
    """Injected external-command boundary used by npx and uvx owners."""

    def run(self, command: PackageCommand) -> PackageCommandResult:
        """Execute one explicit tokenized command without a shell."""


class SubprocessPackageCommandRunner:
    """Production argv runner with private environment and output bounds."""

    def run(self, command: PackageCommand) -> PackageCommandResult:
        if (
            not command.argv
            or not Path(command.argv[0]).is_absolute()
            or command.timeout_seconds <= 0
        ):
            raise AgentInstallError("package_command_invalid")
        environment = {key: value for key, value in command.environment}
        if len(environment) != len(command.environment):
            raise AgentInstallError("package_command_environment_invalid")
        try:
            process = subprocess.Popen(
                command.argv,
                cwd=command.cwd,
                env=environment,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise AgentInstallError("package_command_failed_to_start") from error
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()
        threads = (
            threading.Thread(
                target=_drain_bounded,
                args=(process, process.stdout, stdout, overflow),
                daemon=True,
            ),
            threading.Thread(
                target=_drain_bounded,
                args=(process, process.stderr, stderr, overflow),
                daemon=True,
            ),
        )
        for thread in threads:
            thread.start()
        try:
            return_code = process.wait(timeout=command.timeout_seconds)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait()
            raise AgentInstallError("package_command_timed_out") from error
        finally:
            for thread in threads:
                thread.join(timeout=5)
        if overflow.is_set():
            raise AgentInstallError("package_command_output_too_large")
        return PackageCommandResult(
            return_code=return_code,
            stdout=bytes(stdout),
            stderr=bytes(stderr),
        )


def private_command_environment(
    executable: Path,
    private_home: Path,
    overrides: Mapping[str, str] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Build a credential-free environment with an explicit executable PATH."""
    for relative in ("tmp", ".cache", ".config", ".local/share"):
        (private_home / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
    values = {
        "CI": "1",
        "HOME": str(private_home),
        "NO_COLOR": "1",
        "PATH": os.pathsep.join((str(executable.parent), "/usr/bin", "/bin")),
        "TMPDIR": str(private_home / "tmp"),
        "XDG_CACHE_HOME": str(private_home / ".cache"),
        "XDG_CONFIG_HOME": str(private_home / ".config"),
        "XDG_DATA_HOME": str(private_home / ".local/share"),
    }
    if overrides:
        values.update(overrides)
    return tuple(sorted(values.items()))


def _drain_bounded(
    process: subprocess.Popen[bytes],
    stream,
    destination: bytearray,
    overflow: threading.Event,
) -> None:  # noqa: ANN001
    if stream is None:
        return
    try:
        while chunk := stream.read(64 * 1024):
            remaining = MAX_PACKAGE_COMMAND_OUTPUT_BYTES - len(destination)
            if len(chunk) > remaining:
                destination.extend(chunk[: max(remaining, 0)])
                overflow.set()
                process.kill()
                return
            destination.extend(chunk)
    finally:
        stream.close()
