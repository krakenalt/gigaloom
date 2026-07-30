"""Isolated private tmux terminal instance lifecycle."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import TYPE_CHECKING

from gigaloom.native.terminal.contracts import (
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
)
from gigaloom.native.terminal.liveness import (
    TerminalLiveness,
    TerminalLivenessKind,
)
from gigaloom.native.terminal.tmux import (
    DEFAULT_TMUX_COMMAND_TIMEOUT_SECONDS,
    SubprocessTmuxCommandRunner,
    TmuxCapability,
    TmuxCommandRunner,
    decode_tmux_escaped_bytes,
    tmux_argv,
)

if TYPE_CHECKING:
    from gigaloom.native.terminal.control_bridge import TerminalControlClient


MIN_TERMINAL_ROWS = 2
MAX_TERMINAL_ROWS = 200
MIN_TERMINAL_COLUMNS = 20
MAX_TERMINAL_COLUMNS = 500
MIN_SCROLLBACK_LINES = 100
MAX_SCROLLBACK_LINES = 100_000
DEFAULT_SCROLLBACK_LINES = 10_000
MAX_PRIVATE_SOCKET_PATH_BYTES = 100
DEFAULT_CAPTURE_LINES = 1000
MAX_CAPTURE_LINES = 2000
MAX_CAPTURE_BYTES = 1024 * 1024
_LIVENESS_PATTERN = re.compile(rb"([01])\t(-?[0-9]*)\t([0-9]+)\n?")
_SCREEN_PATTERN = re.compile(rb"([01])\t([0-9]+)\t([0-9]+)\t([0-9]+)\t([0-9]+)\n?")


class TmuxInstanceError(RuntimeError):
    """Raised when one private tmux instance cannot be managed safely."""


@dataclass(frozen=True)
class TmuxLaunchSpec:
    """Ephemeral provider-neutral command inputs for one terminal launch."""

    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = field(default_factory=dict)
    rows: int = 24
    columns: int = 80

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("terminal launch command is required")
        if any(
            not isinstance(argument, str) or not argument or "\x00" in argument
            for argument in self.command
        ):
            raise ValueError("terminal launch command arguments are invalid")
        path = Path(self.cwd).expanduser().resolve()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("terminal launch cwd must be an existing directory")
        object.__setattr__(self, "cwd", path)
        _validate_geometry(self.rows, self.columns)
        for key, value in self.env.items():
            if (
                not isinstance(key, str)
                or not key
                or "=" in key
                or "\x00" in key
                or not isinstance(value, str)
                or "\x00" in value
            ):
                raise ValueError("terminal launch environment is invalid")


@dataclass(frozen=True)
class _TmuxInstancePaths:
    state_directory: Path
    socket_directory: Path
    socket_path: Path
    session_name: str
    pane_target: str


class TmuxTerminalKernel:
    """Launch, inspect, and close one-server-per-terminal tmux instances."""

    def __init__(
        self,
        state_root: str | Path,
        capability: TmuxCapability,
        *,
        runner: TmuxCommandRunner | None = None,
        socket_root: str | Path | None = None,
        scrollback_lines: int = DEFAULT_SCROLLBACK_LINES,
        timeout_seconds: float = DEFAULT_TMUX_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        if (
            isinstance(scrollback_lines, bool)
            or not isinstance(scrollback_lines, int)
            or not MIN_SCROLLBACK_LINES <= scrollback_lines <= MAX_SCROLLBACK_LINES
        ):
            raise ValueError("terminal scrollback limit is invalid")
        if timeout_seconds <= 0:
            raise ValueError("tmux timeout must be positive")
        self.state_root = Path(state_root).expanduser().resolve() / "native-terminals"
        self.socket_root = (
            Path(socket_root).expanduser().resolve()
            if socket_root is not None
            else _default_socket_root()
        )
        self.capability = capability
        self.runner = runner or SubprocessTmuxCommandRunner()
        self.scrollback_lines = scrollback_lines
        self.timeout_seconds = float(timeout_seconds)

    def launch(
        self,
        record: TerminalRecord,
        spec: TmuxLaunchSpec,
    ) -> TerminalState:
        """Launch one exact command in a new private tmux server."""
        if record.state is not TerminalState.STARTING:
            raise TmuxInstanceError("terminal record is not starting")
        _validate_launch_binding(record.identity, spec)
        if not self.capability.available:
            raise TmuxInstanceError("managed tmux capability is unavailable")
        paths = self._paths(record.id)
        self._create_private_directory(self.state_root, paths.state_directory)
        try:
            self._create_private_directory(
                self.socket_root,
                paths.socket_directory,
            )
        except Exception:
            paths.state_directory.rmdir()
            raise
        commands = (
            "start-server",
            ";",
            "set-option",
            "-s",
            "exit-empty",
            "off",
            ";",
            "set-option",
            "-gw",
            "remain-on-exit",
            "on",
            ";",
            "set-option",
            "-gw",
            "history-limit",
            str(self.scrollback_lines),
            ";",
            "set-option",
            "-g",
            "status",
            "off",
            ";",
            "set-option",
            "-gw",
            "automatic-rename",
            "off",
            ";",
            "new-session",
            "-d",
            "-s",
            paths.session_name,
            "-n",
            "terminal",
            "-x",
            str(spec.columns),
            "-y",
            str(spec.rows),
            "-c",
            str(spec.cwd),
            "--",
            *spec.command,
        )
        try:
            result = self.runner.run(
                tmux_argv(self.capability, paths.socket_path, *commands),
                env=_launch_environment(spec.env),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception as exc:
            self._best_effort_close(paths)
            raise TmuxInstanceError("private tmux launch failed") from exc
        if result.returncode != 0:
            self._best_effort_close(paths)
            raise TmuxInstanceError("private tmux launch failed")
        observed = self.liveness(record.id)
        if observed.kind not in {
            TerminalLivenessKind.LIVE,
            TerminalLivenessKind.EXITED,
        }:
            self._best_effort_close(paths)
            raise TmuxInstanceError("private tmux launch is not observable")
        return observed.terminal_state

    def liveness(self, terminal_id: str) -> TerminalLiveness:
        """Inspect the retained pane and distinguish live, dead, and missing."""
        paths = self._paths(terminal_id)
        if not paths.socket_path.exists():
            return TerminalLiveness(TerminalLivenessKind.MISSING)
        try:
            result = self.runner.run(
                tmux_argv(
                    self.capability,
                    paths.socket_path,
                    "list-panes",
                    "-t",
                    paths.pane_target,
                    "-F",
                    "#{pane_dead}\t#{pane_dead_status}\t#{pane_pid}",
                    no_start=True,
                ),
                timeout_seconds=self.timeout_seconds,
            )
        except Exception:
            return TerminalLiveness(
                TerminalLivenessKind.UNKNOWN
                if paths.socket_path.exists()
                else TerminalLivenessKind.MISSING
            )
        if result.returncode != 0:
            return TerminalLiveness(
                TerminalLivenessKind.UNKNOWN
                if paths.socket_path.exists()
                else TerminalLivenessKind.MISSING
            )
        match = _LIVENESS_PATTERN.fullmatch(result.stdout)
        if match is None:
            return TerminalLiveness(TerminalLivenessKind.UNKNOWN)
        pane_pid = int(match.group(3))
        if match.group(1) == b"0":
            return TerminalLiveness(
                TerminalLivenessKind.LIVE,
                pane_pid=pane_pid,
            )
        raw_status = match.group(2)
        exit_status = int(raw_status) if raw_status else None
        return TerminalLiveness(
            TerminalLivenessKind.EXITED,
            pane_pid=pane_pid,
            exit_status=exit_status,
        )

    def close(self, record: TerminalRecord) -> None:
        """Kill only the exact private server derived from this terminal id."""
        paths = self._paths(record.id)
        if paths.socket_path.exists():
            try:
                result = self.runner.run(
                    tmux_argv(
                        self.capability,
                        paths.socket_path,
                        "kill-server",
                        no_start=True,
                    ),
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception as exc:
                raise TmuxInstanceError("private tmux close failed") from exc
            if result.returncode != 0 and paths.socket_path.exists():
                raise TmuxInstanceError("private tmux close failed")
        self._remove_known_empty_instance(paths)

    def capture_seed(
        self,
        terminal_id: str,
        *,
        lines: int = DEFAULT_CAPTURE_LINES,
    ) -> bytes:
        """Capture one bounded screen/history seed with cursor restoration."""
        if (
            isinstance(lines, bool)
            or not isinstance(lines, int)
            or not 1 <= lines <= MAX_CAPTURE_LINES
        ):
            raise ValueError("terminal capture line limit is invalid")
        paths = self._paths(terminal_id)
        if not paths.socket_path.exists():
            raise TmuxInstanceError("private tmux terminal is missing")
        screen = self.runner.run(
            tmux_argv(
                self.capability,
                paths.socket_path,
                "display-message",
                "-p",
                "-t",
                paths.pane_target,
                "#{alternate_on}\t#{cursor_x}\t#{cursor_y}\t#{pane_width}\t#{pane_height}",
                no_start=True,
            ),
            timeout_seconds=self.timeout_seconds,
        )
        match = _SCREEN_PATTERN.fullmatch(screen.stdout)
        if screen.returncode != 0 or screen.stdout_truncated or match is None:
            raise TmuxInstanceError("private tmux screen metadata is invalid")
        alternate = match.group(1) == b"1"
        cursor_x = int(match.group(2))
        cursor_y = int(match.group(3))
        capture_args = [
            "capture-pane",
            "-p",
            "-e",
            "-C",
            "-N",
            "-t",
            paths.pane_target,
        ]
        if alternate:
            capture_args.append("-a")
        else:
            capture_args.extend(("-J", "-S", f"-{lines}"))
        capture = self.runner.run(
            tmux_argv(
                self.capability,
                paths.socket_path,
                *capture_args,
                no_start=True,
            ),
            timeout_seconds=self.timeout_seconds,
            max_output_bytes=MAX_CAPTURE_BYTES,
        )
        if capture.returncode != 0 or capture.stdout_truncated:
            raise TmuxInstanceError("private tmux screen capture failed")
        content = decode_tmux_escaped_bytes(capture.stdout)
        screen_mode = b"\x1b[?1049h" if alternate else b"\x1b[?1049l"
        cursor = f"\x1b[{cursor_y + 1};{cursor_x + 1}H".encode("ascii")
        seed = screen_mode + b"\x1b[2J\x1b[H" + content + cursor
        if len(seed) > MAX_CAPTURE_BYTES:
            raise TmuxInstanceError("private tmux screen seed is too large")
        return seed

    def open_control_mode(self, terminal_id: str) -> TerminalControlClient:
        """Attach a bounded tmux control client to one private session."""
        paths = self._paths(terminal_id)
        if not paths.socket_path.exists():
            raise TmuxInstanceError("private tmux terminal is missing")
        from gigaloom.native.terminal.control_bridge import (
            SubprocessTmuxControlClient,
        )

        return SubprocessTmuxControlClient(
            tmux_argv(
                self.capability,
                paths.socket_path,
                "attach-session",
                "-t",
                paths.session_name,
                no_start=True,
                control_mode=True,
            ),
            pane_target=paths.pane_target,
            session_target=paths.session_name,
            env=_launch_environment({}),
        )

    def _paths(self, terminal_id: str) -> _TmuxInstancePaths:
        digest = hashlib.sha256(terminal_id.encode("utf-8")).hexdigest()
        state_directory = self.state_root / f"term-{digest[:24]}"
        socket_directory = self.socket_root / f"t-{digest[:16]}"
        socket_path = socket_directory / "s"
        if len(os.fsencode(socket_path)) > MAX_PRIVATE_SOCKET_PATH_BYTES:
            raise TmuxInstanceError("private tmux socket path is too long")
        session_name = f"gl_{digest[:24]}"
        return _TmuxInstancePaths(
            state_directory=state_directory,
            socket_directory=socket_directory,
            socket_path=socket_path,
            session_name=session_name,
            pane_target=f"{session_name}:0.0",
        )

    def _create_private_directory(self, root: Path, directory: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise TmuxInstanceError(
                "private terminal directory already exists"
            ) from exc
        if stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise TmuxInstanceError("private terminal directory mode is invalid")

    def _best_effort_close(self, paths: _TmuxInstancePaths) -> None:
        if paths.socket_path.exists():
            try:
                self.runner.run(
                    tmux_argv(
                        self.capability,
                        paths.socket_path,
                        "kill-server",
                        no_start=True,
                    ),
                    timeout_seconds=self.timeout_seconds,
                )
            except Exception:
                return
        self._remove_known_empty_instance(paths)

    def _remove_known_empty_instance(self, paths: _TmuxInstancePaths) -> None:
        try:
            paths.socket_path.unlink(missing_ok=True)
            paths.socket_directory.rmdir()
            paths.state_directory.rmdir()
        except OSError:
            return


def digest_terminal_command(command: tuple[str, ...]) -> str:
    """Hash exact argv boundaries without retaining command content."""
    digest = hashlib.sha256()
    for argument in command:
        encoded = argument.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def digest_terminal_path(path: str | Path) -> str:
    """Hash one resolved filesystem path without retaining it."""
    resolved = str(Path(path).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()


def _validate_launch_binding(
    identity: TerminalIdentity,
    spec: TmuxLaunchSpec,
) -> None:
    if identity.command_digest != digest_terminal_command(spec.command):
        raise TmuxInstanceError("terminal command digest changed")
    if identity.cwd_digest != digest_terminal_path(spec.cwd):
        raise TmuxInstanceError("terminal cwd digest changed")
    if identity.executable_path_digest != digest_terminal_path(spec.command[0]):
        raise TmuxInstanceError("terminal executable digest changed")


def _validate_geometry(rows: int, columns: int) -> None:
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or not MIN_TERMINAL_ROWS <= rows <= MAX_TERMINAL_ROWS
    ):
        raise ValueError("terminal rows are invalid")
    if (
        isinstance(columns, bool)
        or not isinstance(columns, int)
        or not MIN_TERMINAL_COLUMNS <= columns <= MAX_TERMINAL_COLUMNS
    ):
        raise ValueError("terminal columns are invalid")


def _launch_environment(overrides: Mapping[str, str]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(overrides)
    environment.pop("TMUX", None)
    environment.pop("TMUX_PANE", None)
    return environment


def _default_socket_root() -> Path:
    if os.name != "posix" or not hasattr(os, "getuid"):
        raise TmuxInstanceError("private tmux sockets require POSIX")
    return Path("/tmp") / f"gigaloom-{os.getuid()}" / "tmux"
