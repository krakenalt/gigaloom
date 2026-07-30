"""Launch the real Codex TUI inside one managed terminal."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import threading

from gigaloom.native.codex_operator.app_server import (
    CodexAppServerLaunchSpec,
    CodexAppServerProcess,
)
from gigaloom.native.codex_operator.contracts import (
    CodexCapabilityState,
    CodexCompatibilitySnapshot,
)
from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalAccess,
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
    TmuxLaunchSpec,
    TmuxTerminalKernel,
    digest_terminal_command,
    digest_terminal_path,
)


MAX_CODEX_CONFIG_BYTES = 64 * 1024
MAX_CODEX_ARGUMENT_CHARS = 8192
_SAFE_NAME = re.compile(r"[A-Za-z0-9._:@+~-]{1,256}")


class CodexManagedLaunchError(RuntimeError):
    """Raised when native managed Codex launch cannot be admitted."""


@dataclass(frozen=True)
class CodexManagedLaunchRequest:
    """Reviewed inputs for one real native Codex TUI launch."""

    access: TerminalAccess
    terminal_name: str
    session_key: str
    command: tuple[str, ...]
    executable_version: str
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    config_toml: str | None = None
    tui_args: tuple[str, ...] = ()
    native_session_id: str | None = None
    rows: int = 24
    columns: int = 80

    def __post_init__(self) -> None:
        if not isinstance(self.access, TerminalAccess):
            raise ValueError("Codex launch access is invalid")
        for value, label in (
            (self.terminal_name, "terminal name"),
            (self.session_key, "session key"),
            (self.executable_version, "executable version"),
        ):
            if not isinstance(value, str) or _SAFE_NAME.fullmatch(value) is None:
                raise ValueError(f"Codex launch {label} is invalid")
        if not self.command:
            raise ValueError("Codex launch command is required")
        _validate_arguments((*self.command, *self.tui_args))
        cwd = Path(self.cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError("Codex launch cwd is invalid")
        object.__setattr__(self, "cwd", cwd)
        if self.config_toml is not None:
            content = self.config_toml.encode("utf-8")
            if not content or len(content) > MAX_CODEX_CONFIG_BYTES or b"\0" in content:
                raise ValueError("Codex launch config is invalid")
        for key, value in self.env.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\0" in key
                or not isinstance(value, str)
                or "\0" in value
            ):
                raise ValueError("Codex launch environment is invalid")


@dataclass(frozen=True)
class CodexManagedLaunch:
    """Content-free result of one managed native Codex launch."""

    terminal: TerminalRecord
    compatibility_status: CodexCapabilityState
    private_home_digest: str
    app_server_command_digest: str


class CodexManagedTerminalLauncher:
    """Bind a private app-server and real remote Codex TUI to one terminal."""

    def __init__(
        self,
        state_root: str | Path,
        registry: ManagedTerminalRegistry,
        terminal_kernel: TmuxTerminalKernel,
        compatibility: CodexCompatibilitySnapshot,
        *,
        socket_root: str | Path | None = None,
        app_server_factory: (
            Callable[[CodexAppServerLaunchSpec], CodexAppServerProcess] | None
        ) = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.socket_root = (
            Path(socket_root).expanduser().resolve()
            if socket_root is not None
            else _default_socket_root()
        )
        self.registry = registry
        self.terminal_kernel = terminal_kernel
        self.compatibility = compatibility
        self.app_server_factory = app_server_factory or CodexAppServerProcess
        self._processes: dict[str, CodexAppServerProcess] = {}
        self._process_lock = threading.Lock()

    def launch(self, request: CodexManagedLaunchRequest) -> CodexManagedLaunch:
        """Launch the real TUI only for exact admitted structured evidence."""
        if not self.compatibility.structured:
            raise CodexManagedLaunchError(
                "structured native Codex launch is not admitted"
            )
        paths = self._paths(request)
        remote = f"unix://{paths.socket_path}"
        tui_command = (
            *request.command,
            "--remote",
            remote,
            "--strict-config",
            "--cd",
            str(request.cwd),
            *request.tui_args,
        )
        environment = {**request.env, "CODEX_HOME": str(paths.home)}
        terminal_spec = TmuxLaunchSpec(
            command=tui_command,
            cwd=request.cwd,
            env=environment,
            rows=request.rows,
            columns=request.columns,
        )
        identity = TerminalIdentity(
            owner_id=request.access.owner_id,
            workspace_id=request.access.workspace_id,
            session_id=request.access.session_id,
            terminal_name=request.terminal_name,
            session_key=request.session_key,
            native_harness_id="codex-cli",
            native_session_id=request.native_session_id,
            command_digest=digest_terminal_command(tui_command),
            cwd_digest=digest_terminal_path(request.cwd),
            executable_path_digest=digest_terminal_path(tui_command[0]),
            executable_version=request.executable_version,
        )
        app_server_spec = CodexAppServerLaunchSpec(
            command=(
                *request.command,
                "app-server",
                "--listen",
                remote,
                "--strict-config",
            ),
            env=environment,
            cwd=request.cwd,
            socket_directory=paths.socket_directory,
            socket_path=paths.socket_path,
        )

        def launch_terminal(record: TerminalRecord) -> TerminalState:
            self._prepare_home(paths.home, request.config_toml)
            app_server = self.app_server_factory(app_server_spec)
            app_server.start()
            try:
                state = self.terminal_kernel.launch(record, terminal_spec)
            except Exception:
                app_server.close()
                raise
            with self._process_lock:
                self._processes[record.id] = app_server
            return state

        terminal = self.registry.ensure(identity, launch=launch_terminal)
        with self._process_lock:
            app_server = self._processes.get(terminal.id)
        if app_server is None or not app_server.alive:
            raise CodexManagedLaunchError(
                "managed Codex app-server ownership is unavailable"
            )
        return CodexManagedLaunch(
            terminal=terminal,
            compatibility_status=self.compatibility.status,
            private_home_digest=digest_terminal_path(paths.home),
            app_server_command_digest=app_server.spec.command_digest,
        )

    def close(
        self,
        terminal_id: str,
        access: TerminalAccess,
        *,
        expected_revision: int | None = None,
    ) -> TerminalRecord:
        """Close the exact terminal and its paired private app-server."""

        def close_instance(record: TerminalRecord) -> None:
            with self._process_lock:
                app_server = self._processes.pop(record.id, None)
            try:
                self.terminal_kernel.close(record)
            finally:
                if app_server is not None:
                    app_server.close()

        return self.registry.close(
            terminal_id,
            access,
            close_instance=close_instance,
            expected_revision=expected_revision,
        )

    def _prepare_home(self, home: Path, config_toml: str | None) -> None:
        home.mkdir(parents=True, mode=0o700)
        os.chmod(home, 0o700)
        if config_toml is None:
            return
        config_path = home / "config.toml"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(config_path, flags, 0o600)
        try:
            os.write(descriptor, config_toml.encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _paths(self, request: CodexManagedLaunchRequest) -> "_CodexPrivatePaths":
        digest = hashlib.sha256()
        for value in (
            request.access.owner_id,
            request.access.workspace_id,
            request.access.session_id,
            request.terminal_name,
            request.session_key,
        ):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        key = digest.hexdigest()
        home = self.state_root / "native" / "codex" / "operator" / key[:32] / "home"
        socket_directory = self.socket_root / f"c-{key[:20]}"
        return _CodexPrivatePaths(
            home=home,
            socket_directory=socket_directory,
            socket_path=socket_directory / "s",
        )


@dataclass(frozen=True)
class _CodexPrivatePaths:
    home: Path
    socket_directory: Path
    socket_path: Path


def _validate_arguments(arguments: tuple[str, ...]) -> None:
    if any(
        not isinstance(value, str)
        or not value
        or "\0" in value
        or len(value) > MAX_CODEX_ARGUMENT_CHARS
        for value in arguments
    ):
        raise ValueError("Codex launch arguments are invalid")


def _default_socket_root() -> Path:
    if os.name != "posix" or not hasattr(os, "getuid"):
        raise CodexManagedLaunchError("native Codex remote TUI requires POSIX")
    return Path("/tmp") / f"gigaloom-{os.getuid()}" / "codex"
