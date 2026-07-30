"""Private Unix-socket Codex app-server process lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Protocol


APP_SERVER_START_TIMEOUT_SECONDS = 5.0
APP_SERVER_STOP_TIMEOUT_SECONDS = 2.0
MAX_APP_SERVER_SOCKET_PATH_BYTES = 100


class CodexAppServerProcessError(RuntimeError):
    """Raised when the private app-server lifecycle cannot be proven."""


class _Process(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class CodexAppServerLaunchSpec:
    """Secret-free inputs for one private app-server process."""

    command: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    socket_directory: Path
    socket_path: Path

    def __post_init__(self) -> None:
        if not self.command or any(
            not isinstance(value, str) or not value or "\x00" in value
            for value in self.command
        ):
            raise ValueError("Codex app-server command is invalid")
        cwd = Path(self.cwd).expanduser().resolve()
        socket_directory = Path(self.socket_directory).expanduser().resolve()
        socket_path = Path(self.socket_path).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError("Codex app-server cwd is invalid")
        if socket_path.parent != socket_directory:
            raise ValueError("Codex app-server socket binding is invalid")
        if len(os.fsencode(socket_path)) > MAX_APP_SERVER_SOCKET_PATH_BYTES:
            raise ValueError("Codex app-server socket path is too long")
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "socket_directory", socket_directory)
        object.__setattr__(self, "socket_path", socket_path)

    @property
    def command_digest(self) -> str:
        """Return the argv-boundary digest without exposing the socket."""
        digest = hashlib.sha256()
        for argument in self.command:
            value = argument.encode("utf-8")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.hexdigest()


class CodexAppServerProcess:
    """Supervise one exact private Unix-socket app-server."""

    def __init__(
        self,
        spec: CodexAppServerLaunchSpec,
        *,
        popen: Callable[..., _Process] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        socket_ready: Callable[[Path], bool] | None = None,
    ) -> None:
        self.spec = spec
        self._popen = popen
        self._monotonic = monotonic
        self._sleep = sleep
        self._socket_ready = socket_ready or _is_unix_socket
        self._process: _Process | None = None

    @property
    def alive(self) -> bool:
        """Return whether the owned app-server process remains live."""
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start and admit the process only after its private socket is ready."""
        if self._process is not None:
            raise CodexAppServerProcessError("Codex app-server was already started")
        self.spec.socket_directory.mkdir(parents=True, mode=0o700)
        os.chmod(self.spec.socket_directory, 0o700)
        if self.spec.socket_path.exists():
            raise CodexAppServerProcessError("Codex app-server socket already exists")
        environment = dict(os.environ)
        environment.update(self.spec.env)
        try:
            process = self._popen(
                self.spec.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(self.spec.cwd),
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            raise CodexAppServerProcessError(
                "Codex app-server process failed to start"
            ) from exc
        self._process = process
        deadline = self._monotonic() + APP_SERVER_START_TIMEOUT_SECONDS
        while self._monotonic() < deadline:
            if process.poll() is not None:
                self._cleanup_socket()
                raise CodexAppServerProcessError(
                    "Codex app-server exited before socket readiness"
                )
            if self._socket_ready(self.spec.socket_path):
                return
            self._sleep(0.01)
        self.close()
        raise CodexAppServerProcessError("Codex app-server socket readiness timed out")

    def close(self) -> None:
        """Stop only the owned process and remove its known socket."""
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=APP_SERVER_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=APP_SERVER_STOP_TIMEOUT_SECONDS)
        self._process = None
        self._cleanup_socket()

    def _cleanup_socket(self) -> None:
        try:
            self.spec.socket_path.unlink(missing_ok=True)
            self.spec.socket_directory.rmdir()
        except OSError:
            return


def _is_unix_socket(path: Path) -> bool:
    try:
        return stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False
