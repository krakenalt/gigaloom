"""Bounded tmux control-mode to binary WebSocket bridge."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import select
import subprocess
import threading
from typing import Protocol

from gigaloom.native.terminal.contracts import TerminalRecord, TerminalState
from gigaloom.native.terminal.liveness import (
    TerminalLiveness,
    TerminalLivenessKind,
)
from gigaloom.native.terminal.registry import (
    ManagedTerminalRegistry,
    TerminalAccessDeniedError,
    TerminalConflictError,
    TerminalNotFoundError,
)
from gigaloom.native.terminal.security import (
    TerminalAttachRejectedError,
    TerminalAttachRequest,
    TerminalOriginPolicy,
    authorize_terminal_record,
)
from gigaloom.native.terminal.tmux import decode_tmux_escaped_bytes
from gigaloom.native.terminal.websocket_protocol import (
    MAX_TERMINAL_INPUT_CHUNK_BYTES,
    MAX_TERMINAL_OUTPUT_FRAME_BYTES,
    MAX_TERMINAL_OUTPUT_QUEUE_BYTES,
    TerminalInputFrame,
    TerminalResizeFrame,
    TerminalWebSocketCloseCode,
    parse_terminal_client_frame,
)


MAX_TMUX_CONTROL_LINE_BYTES = 256 * 1024


class TerminalBackpressureError(BufferError):
    """Raised instead of allowing an unbounded slow-client queue."""


class TmuxControlReadTimeout(TimeoutError):
    """Raised when a quiet control client has no line within its poll budget."""


class TerminalControlClient(Protocol):
    """One attached tmux control-mode client."""

    def read_line(self) -> bytes:
        """Read one bounded tmux control-mode line."""

    def send_input(self, data: bytes) -> None:
        """Send one bounded byte-exact chunk to the pane."""

    def resize(self, *, rows: int, columns: int) -> None:
        """Resize the exact managed window."""

    def close(self) -> None:
        """Detach this client without stopping the terminal."""


class TerminalControlBackend(Protocol):
    """Authorized backend operations required by the bridge."""

    def capture_seed(self, terminal_id: str) -> bytes:
        """Return one bounded terminal seed."""

    def open_control_mode(self, terminal_id: str) -> TerminalControlClient:
        """Open a control client for an already authorized terminal."""

    def liveness(self, terminal_id: str) -> TerminalLiveness:
        """Return pane-aware liveness."""


class BoundedTerminalOutputQueue:
    """Coalesce binary output while enforcing frame and queue budgets."""

    def __init__(
        self,
        *,
        max_frame_bytes: int = MAX_TERMINAL_OUTPUT_FRAME_BYTES,
        max_queue_bytes: int = MAX_TERMINAL_OUTPUT_QUEUE_BYTES,
    ) -> None:
        if max_frame_bytes < 1 or max_queue_bytes < max_frame_bytes:
            raise ValueError("terminal output queue limits are invalid")
        self.max_frame_bytes = max_frame_bytes
        self.max_queue_bytes = max_queue_bytes
        self._frames: deque[bytes] = deque()
        self._queued_bytes = 0
        self._lock = threading.Lock()

    @property
    def queued_bytes(self) -> int:
        """Return the current bounded queue size."""
        with self._lock:
            return self._queued_bytes

    def push(self, data: bytes) -> None:
        """Split and coalesce data or reject slow-client overflow."""
        if not data:
            return
        with self._lock:
            if self._queued_bytes + len(data) > self.max_queue_bytes:
                raise TerminalBackpressureError("terminal output queue overflow")
            offset = 0
            while offset < len(data):
                if self._frames and len(self._frames[-1]) < self.max_frame_bytes:
                    available = self.max_frame_bytes - len(self._frames[-1])
                    chunk = data[offset : offset + available]
                    self._frames[-1] += chunk
                else:
                    chunk = data[offset : offset + self.max_frame_bytes]
                    self._frames.append(chunk)
                offset += len(chunk)
                self._queued_bytes += len(chunk)

    def pop(self) -> bytes | None:
        """Pop the next binary frame."""
        with self._lock:
            if not self._frames:
                return None
            frame = self._frames.popleft()
            self._queued_bytes -= len(frame)
            return frame


class TerminalControlSession:
    """One authorized browser projection over a tmux control client."""

    def __init__(
        self,
        record: TerminalRecord,
        client: TerminalControlClient,
        *,
        liveness: Callable[[str], object],
        seed: bytes,
        queue: BoundedTerminalOutputQueue | None = None,
    ) -> None:
        self.record = record
        self.client = client
        self._liveness = liveness
        self.queue = queue or BoundedTerminalOutputQueue()
        self.closed_code: TerminalWebSocketCloseCode | None = None
        self.closed_reason: str | None = None
        self._pending_resize: TerminalResizeFrame | None = None
        self.queue.push(seed)

    def receive(self, frame: bytes | str) -> None:
        """Apply one strict browser frame to the exact control client."""
        if self.closed_code is not None:
            raise RuntimeError("terminal control session is closed")
        parsed = parse_terminal_client_frame(frame)
        if isinstance(parsed, TerminalResizeFrame):
            if parsed.revision != self.record.revision:
                raise TerminalAttachRejectedError(
                    TerminalWebSocketCloseCode.FORBIDDEN,
                    "terminal_revision_changed",
                )
            self._pending_resize = parsed
            return
        if not isinstance(parsed, TerminalInputFrame):
            raise AssertionError("unknown terminal client frame")
        self.flush_resize()
        for offset in range(0, len(parsed.data), MAX_TERMINAL_INPUT_CHUNK_BYTES):
            self.client.send_input(
                parsed.data[offset : offset + MAX_TERMINAL_INPUT_CHUNK_BYTES]
            )

    def pump_once(self) -> bytes | None:
        """Read one control line and queue one or more binary frames."""
        if self.closed_code is not None:
            return None
        self.flush_resize()
        try:
            line = self.client.read_line()
        except TmuxControlReadTimeout:
            return self.queue.pop()
        except TerminalBackpressureError:
            self._close(
                TerminalWebSocketCloseCode.INTERNAL_ERROR,
                "tmux_control_output_invalid",
            )
            return None
        if not line:
            self._close_from_liveness()
            return None
        if line.startswith(b"%output "):
            parts = line.rstrip(b"\n").split(b" ", 2)
            if len(parts) != 3:
                self._close(
                    TerminalWebSocketCloseCode.INTERNAL_ERROR,
                    "tmux_control_output_invalid",
                )
                return None
            try:
                self.queue.push(decode_tmux_escaped_bytes(parts[2]))
            except ValueError:
                self._close(
                    TerminalWebSocketCloseCode.INTERNAL_ERROR,
                    "tmux_control_output_invalid",
                )
                return None
            except TerminalBackpressureError:
                self._close(
                    TerminalWebSocketCloseCode.BACKPRESSURE,
                    "terminal_output_backpressure",
                )
                return None
        elif line.startswith((b"%pane-died", b"%pane-exited")):
            self._close(
                TerminalWebSocketCloseCode.EXITED,
                "terminal_exited",
            )
        elif line.startswith(b"%exit"):
            self._close_from_liveness()
        return self.queue.pop()

    def next_frame(self) -> bytes | None:
        """Return already queued binary output without reading tmux."""
        return self.queue.pop()

    def flush_resize(self) -> None:
        """Apply only the latest pending resize from a bounded resize storm."""
        pending = self._pending_resize
        if pending is None:
            return
        self._pending_resize = None
        self.client.resize(rows=pending.rows, columns=pending.columns)

    def close(self) -> None:
        """Detach this Web client while preserving terminal lifecycle."""
        self._close(
            TerminalWebSocketCloseCode.DETACHED,
            "terminal_detached",
        )

    def close_with(
        self,
        code: TerminalWebSocketCloseCode,
        reason: str,
    ) -> None:
        """Close this client with one typed public WebSocket outcome."""
        if not isinstance(code, TerminalWebSocketCloseCode):
            raise ValueError("terminal WebSocket close code is invalid")
        if not isinstance(reason, str) or not reason or len(reason) > 123:
            raise ValueError("terminal WebSocket close reason is invalid")
        self._close(code, reason)

    def _close_from_liveness(self) -> None:
        observed = self._liveness(self.record.id)
        kind = getattr(observed, "kind", TerminalLivenessKind.UNKNOWN)
        if kind is TerminalLivenessKind.LIVE:
            self._close(
                TerminalWebSocketCloseCode.DETACHED,
                "terminal_detached",
            )
        elif kind is TerminalLivenessKind.EXITED:
            self._close(
                TerminalWebSocketCloseCode.EXITED,
                "terminal_exited",
            )
        elif kind is TerminalLivenessKind.MISSING:
            self._close(
                TerminalWebSocketCloseCode.NOT_FOUND,
                "terminal_not_found",
            )
        else:
            self._close(
                TerminalWebSocketCloseCode.INTERNAL_ERROR,
                "terminal_liveness_unknown",
            )

    def _close(
        self,
        code: TerminalWebSocketCloseCode,
        reason: str,
    ) -> None:
        if self.closed_code is not None:
            return
        self.closed_code = code
        self.closed_reason = reason
        self.client.close()


class TerminalControlBridge:
    """Authorize before resolving any private tmux target."""

    def __init__(
        self,
        registry: ManagedTerminalRegistry,
        backend: TerminalControlBackend,
        origin_policy: TerminalOriginPolicy,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.origin_policy = origin_policy

    def open(self, request: TerminalAttachRequest) -> TerminalControlSession:
        """Authorize an attach, seed it, then open control mode."""
        self.origin_policy.authorize(request.origin)
        try:
            record = self.registry.get(
                request.terminal_id,
                request.access,
                expected_revision=request.revision,
            )
        except TerminalNotFoundError as exc:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.NOT_FOUND,
                "terminal_not_found",
            ) from exc
        except (TerminalAccessDeniedError, TerminalConflictError) as exc:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.FORBIDDEN,
                "terminal_binding_forbidden",
            ) from exc
        authorize_terminal_record(request, record)
        if record.state in {TerminalState.EXITED, TerminalState.CLOSED}:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.EXITED,
                "terminal_exited",
            )
        if record.state not in {
            TerminalState.RUNNING,
            TerminalState.ATTACHED,
            TerminalState.DETACHED,
        }:
            raise TerminalAttachRejectedError(
                TerminalWebSocketCloseCode.NOT_FOUND,
                "terminal_not_found",
            )
        seed = self.backend.capture_seed(record.id)
        client = self.backend.open_control_mode(record.id)
        return TerminalControlSession(
            record,
            client,
            liveness=self.backend.liveness,
            seed=seed,
        )


class SubprocessTmuxControlClient:
    """A bounded synchronous tmux `-C` client for one private pane."""

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        pane_target: str,
        session_target: str,
        env: dict[str, str] | None = None,
        read_timeout_seconds: float = 1.0,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        if read_timeout_seconds <= 0:
            raise ValueError("tmux control read timeout must be positive")
        self.pane_target = pane_target
        self.session_target = session_target
        self.read_timeout_seconds = float(read_timeout_seconds)
        self._write_lock = threading.Lock()
        self._process = process_factory(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            bufsize=0,
        )
        if self._process.stdin is None or self._process.stdout is None:
            self._process.kill()
            raise RuntimeError("tmux control mode pipes are unavailable")

    def read_line(self) -> bytes:
        """Read one line and reject an oversized control notification."""
        if self._process.stdout is None:
            return b""
        ready, _, _ = select.select(
            (self._process.stdout,),
            (),
            (),
            self.read_timeout_seconds,
        )
        if not ready:
            if self._process.poll() is not None:
                return b""
            raise TmuxControlReadTimeout("tmux control read timed out")
        line = self._process.stdout.readline(MAX_TMUX_CONTROL_LINE_BYTES + 1)
        if len(line) > MAX_TMUX_CONTROL_LINE_BYTES:
            raise TerminalBackpressureError("tmux control line is too large")
        return line

    def send_input(self, data: bytes) -> None:
        """Encode exact bytes as bounded hexadecimal send-keys input."""
        if not data or len(data) > MAX_TERMINAL_INPUT_CHUNK_BYTES:
            raise ValueError("terminal input chunk size is invalid")
        command = "send-keys -H -t {} {}".format(
            self.pane_target,
            " ".join(f"{value:02x}" for value in data),
        )
        self._write_command(command)

    def resize(self, *, rows: int, columns: int) -> None:
        """Resize the exact managed session window."""
        self._write_command(
            f"resize-window -t {self.session_target}:0 -x {columns} -y {rows}"
        )

    def close(self) -> None:
        """Detach only this control client and bound process teardown."""
        if self._process.poll() is not None:
            return
        try:
            self._write_command("detach-client")
            self._process.wait(timeout=1.0)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self._process.terminate()
            try:
                self._process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=1.0)

    def _write_command(self, command: str) -> None:
        encoded = command.encode("ascii") + b"\n"
        with self._write_lock:
            if self._process.stdin is None:
                raise BrokenPipeError("tmux control stdin is closed")
            self._process.stdin.write(encoded)
            self._process.stdin.flush()
