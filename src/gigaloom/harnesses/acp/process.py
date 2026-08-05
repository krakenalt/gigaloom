"""Pinned local ACP stdio process construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import threading

from gigaloom.harnesses.acp.contracts import (
    AcpExecutableIdentity,
    AcpLimits,
    AcpProcessSpec,
)
from gigaloom.harnesses.acp.errors import AcpProcessError
from gigaloom.structured_processes import StdioJsonRpcTransport


DEFAULT_ENVIRONMENT_ALLOWLIST = frozenset(
    """APPDATA HOMEDRIVE HOMEPATH LANG LC_ALL LOCALAPPDATA PATH PATHEXT SYSTEMDRIVE
SYSTEMROOT TEMP TMP TMPDIR USERPROFILE""".split()
)
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def pin_acp_process(
    command: Sequence[str],
    *,
    cwd: str | Path,
    environment: Mapping[str, str],
    allowed_environment: frozenset[str] = frozenset(),
    approved_secret_names: frozenset[str] = frozenset(),
) -> AcpProcessSpec:
    """Resolve and pin one executable while filtering its subprocess environment."""
    tokens = tuple(command)
    if not tokens or any(not isinstance(token, str) or not token for token in tokens):
        raise ValueError("ACP command must contain non-empty string tokens")
    requested = Path(tokens[0]).expanduser()
    if not requested.is_absolute():
        raise ValueError("ACP executable must already be resolved to an absolute path")
    try:
        executable_path = requested.resolve(strict=True)
        executable = _executable_identity(executable_path)
    except (OSError, ValueError) as exc:
        raise AcpProcessError("ACP executable cannot be pinned") from exc
    workspace = Path(cwd).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("ACP cwd must be an existing directory")

    allowed_names = DEFAULT_ENVIRONMENT_ALLOWLIST | allowed_environment
    approved_names = approved_secret_names & allowed_names
    filtered: list[tuple[str, str]] = []
    for name in sorted(allowed_names):
        value = environment.get(name)
        if value is None:
            continue
        if (
            any(marker in name.upper() for marker in _SECRET_MARKERS)
            and name not in approved_names
        ):
            continue
        if not isinstance(value, str):
            raise ValueError("ACP environment values must be strings")
        filtered.append((name, value))
    return AcpProcessSpec(
        command=(executable.path, *tokens[1:]),
        cwd=str(workspace),
        environment=tuple(filtered),
        executable=executable,
    )


class AcpStdioTransport(StdioJsonRpcTransport):
    """ACP stdio transport whose shutdown owns the full POSIX process group."""

    def terminate(self) -> None:
        """Request bounded termination for the full owned process group."""
        self._signal_group(signal.SIGTERM, force=False)

    def kill(self) -> None:
        """Force termination for the full owned process group."""
        self._signal_group(signal.SIGKILL, force=True)

    def wait(self, timeout: float) -> int | None:
        """Wait, then release every inherited pipe deterministically."""
        result = super().wait(timeout)
        process = self._require_process()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
        return result

    def _signal_group(self, requested: signal.Signals, *, force: bool) -> None:
        if os.name != "posix":
            (super().kill if force else super().terminate)()
            return
        self._closing.set()
        if not self.alive:
            return
        process = self._require_process()
        try:
            os.killpg(os.getpgid(process.pid), requested)
        except ProcessLookupError:
            return


class AcpTransportFactory:
    """Create generation-labelled transports after rechecking file identity."""

    def __init__(
        self,
        spec: AcpProcessSpec,
        limits: AcpLimits,
        *,
        launch_command: Sequence[str] | None = None,
    ) -> None:
        self.spec = spec
        self.limits = limits
        self._launch_command = tuple(launch_command or spec.command)
        if (
            not self._launch_command
            or not Path(self._launch_command[0]).is_absolute()
            or any(not token or "\x00" in token for token in self._launch_command)
        ):
            raise ValueError("ACP launch command is invalid")
        self._generation = 0
        self._lock = threading.Lock()

    def __call__(self) -> AcpStdioTransport:
        if (
            _executable_identity(Path(self.spec.executable.path))
            != self.spec.executable
        ):
            raise AcpProcessError("ACP executable identity changed after admission")
        with self._lock:
            self._generation += 1
            generation = self._generation
        return AcpStdioTransport(
            command=self._launch_command,
            runtime_id=(f"acp-{self.spec.executable.fingerprint[:16]}-{generation}"),
            env=self.spec.env,
            cwd=self.spec.cwd,
            read_queue_size=self.limits.max_inbound_messages,
            max_stderr_bytes=self.limits.max_stderr_bytes,
        )


def _executable_identity(path: Path) -> AcpExecutableIdentity:
    file_stat = path.stat()
    if not stat.S_ISREG(file_stat.st_mode) or not os.access(path, os.X_OK):
        raise AcpProcessError("ACP executable is not an executable file")
    identity = {
        "path": str(path),
        "device": file_stat.st_dev,
        "inode": file_stat.st_ino,
        "size": file_stat.st_size,
        "modified_ns": file_stat.st_mtime_ns,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return AcpExecutableIdentity(fingerprint=fingerprint, **identity)
