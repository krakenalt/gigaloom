"""Bounded JSON-RPC clients for Codex app-server process transports."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
import hashlib
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
from typing import Any


CODEX_RPC_TIMEOUT_SECONDS = 10.0
MAX_CODEX_RPC_MESSAGE_CHARS = 2 * 1024 * 1024
MAX_CODEX_RPC_NOTIFICATIONS = 1024
MAX_CODEX_RPC_STDERR_CHARS = 16 * 1024
MAX_CODEX_WEBSOCKET_HEADERS_BYTES = 16 * 1024


class CodexOperatorProtocolError(RuntimeError):
    """Raised when the private app-server violates the admitted contract."""


class CodexStdioJsonRpcClient:
    """Own one JSONL process and demultiplex bounded JSON-RPC messages."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        env: Mapping[str, str],
        cwd: str | Path,
        runtime_id: str,
        initialize_title: str = "GigaLoom",
        initialize_capabilities: Mapping[str, Any] | None = None,
        protocol_error: type[RuntimeError] = CodexOperatorProtocolError,
        error_message: Callable[[str, Any], str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("Codex app-server command is required")
        self.runtime_id = runtime_id
        self.command = command
        self._protocol_error = protocol_error
        self._error_message = error_message or (
            lambda method, _error: f"Codex app-server request failed: {method}"
        )
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env),
            cwd=str(cwd),
            start_new_session=True,
            bufsize=1,
        )
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[
            str | int,
            queue.Queue[Mapping[str, Any]],
        ] = {}
        self._messages: queue.Queue[Mapping[str, Any]] = queue.Queue(
            maxsize=MAX_CODEX_RPC_NOTIFICATIONS
        )
        self._next_id = 1
        self._stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "gigaloom",
                        "title": initialize_title,
                        "version": "1",
                    },
                    "capabilities": dict(
                        initialize_capabilities
                        if initialize_capabilities is not None
                        else {"optOutNotificationMethods": []}
                    ),
                },
                timeout=CODEX_RPC_TIMEOUT_SECONDS,
            )
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    @property
    def alive(self) -> bool:
        """Return whether the JSON-RPC proxy process remains live."""
        return self._process.poll() is None

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float,
    ) -> Mapping[str, Any]:
        """Send one request and return an exact result object."""
        request_id = self._allocate_id()
        response_queue: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        try:
            self._send({"id": request_id, "method": method, "params": dict(params)})
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise self._protocol_error(
                    f"Codex app-server request timed out: {method}"
                ) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            raise self._protocol_error(
                self._error_message(method, response.get("error"))
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise self._protocol_error(
                f"Codex app-server {method} returned an invalid result"
            )
        return dict(result)

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        """Send one client notification."""
        payload: dict[str, Any] = {"method": method}
        if params:
            payload["params"] = dict(params)
        self._send(payload)

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Return one bounded server notification or request."""
        try:
            return self._messages.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def respond(
        self,
        request_id: str | int,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Answer one server-initiated request."""
        payload: dict[str, Any] = {"id": request_id}
        if error is not None:
            payload["error"] = dict(error)
        else:
            payload["result"] = dict(result or {})
        self._send(payload)

    def close(self) -> None:
        """Stop only the owned JSON-RPC proxy process."""
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2.0)

    def _allocate_id(self) -> int:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            return request_id

    def _send(self, payload: Mapping[str, Any]) -> None:
        if self._process.stdin is None or not self.alive:
            raise self._protocol_error("Codex app-server transport is closed")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        if len(line) > MAX_CODEX_RPC_MESSAGE_CHARS:
            raise self._protocol_error("Codex app-server request is too large")
        try:
            with self._write_lock:
                self._process.stdin.write(line)
                self._process.stdin.flush()
        except OSError as exc:
            raise self._protocol_error(
                "Codex app-server transport write failed"
            ) from exc

    def _read_stdout(self) -> None:
        if self._process.stdout is None:
            return
        for line in self._process.stdout:
            if len(line) > MAX_CODEX_RPC_MESSAGE_CHARS:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping):
                continue
            request_id = payload.get("id")
            if request_id is not None and "method" not in payload:
                with self._pending_lock:
                    target = self._pending.get(request_id)
                if target is not None:
                    target.put(dict(payload))
                    continue
            try:
                self._messages.put_nowait(dict(payload))
            except queue.Full:
                break

    def _read_stderr(self) -> None:
        if self._process.stderr is None:
            return
        total = 0
        for line in self._process.stderr:
            self._stderr.append(line)
            total += len(line)
            while self._stderr and total > MAX_CODEX_RPC_STDERR_CHARS:
                total -= len(self._stderr.pop(0))


class CodexUnixWebSocketJsonRpcClient:
    """Speak JSON-RPC over WebSocket on a private app-server Unix socket."""

    def __init__(
        self,
        *,
        socket_path: str | Path,
        runtime_id: str,
        connect: Callable[[Path], socket.socket] | None = None,
        timeout_seconds: float = CODEX_RPC_TIMEOUT_SECONDS,
    ) -> None:
        path = Path(socket_path).expanduser().resolve()
        if timeout_seconds <= 0:
            raise ValueError("Codex app-server timeout is invalid")
        self.runtime_id = runtime_id
        self.socket_path = path
        self.timeout_seconds = float(timeout_seconds)
        self._socket = (connect or _connect_unix)(path)
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[
            str | int,
            queue.Queue[Mapping[str, Any]],
        ] = {}
        self._messages: queue.Queue[Mapping[str, Any]] = queue.Queue(
            maxsize=MAX_CODEX_RPC_NOTIFICATIONS
        )
        self._next_id = 1
        self._closed = False
        try:
            self._socket.settimeout(self.timeout_seconds)
            self._handshake()
            self._socket.settimeout(None)
            threading.Thread(target=self._read_frames, daemon=True).start()
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "gigaloom",
                        "title": "GigaLoom",
                        "version": "1",
                    },
                    "capabilities": {"optOutNotificationMethods": []},
                },
                timeout=self.timeout_seconds,
            )
            self.notify("initialized", {})
        except Exception:
            self.close()
            raise

    @property
    def alive(self) -> bool:
        """Return whether the private WebSocket remains open."""
        return not self._closed

    def request(
        self,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout: float,
    ) -> Mapping[str, Any]:
        """Send one request and return an exact result object."""
        request_id = self._allocate_id()
        response_queue: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        try:
            self._send_json(
                {"id": request_id, "method": method, "params": dict(params)}
            )
            try:
                response = response_queue.get(timeout=timeout)
            except queue.Empty as exc:
                raise CodexOperatorProtocolError(
                    f"Codex app-server request timed out: {method}"
                ) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            raise CodexOperatorProtocolError(
                f"Codex app-server request failed: {method}"
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise CodexOperatorProtocolError(
                f"Codex app-server {method} returned an invalid result"
            )
        return dict(result)

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        """Send one client notification."""
        payload: dict[str, Any] = {"method": method}
        if params:
            payload["params"] = dict(params)
        self._send_json(payload)

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None:
        """Return one bounded server notification or request."""
        try:
            return self._messages.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def respond(
        self,
        request_id: str | int,
        *,
        result: Mapping[str, Any] | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        """Answer one server-initiated request."""
        payload: dict[str, Any] = {"id": request_id}
        if error is not None:
            payload["error"] = dict(error)
        else:
            payload["result"] = dict(result or {})
        self._send_json(payload)

    def close(self) -> None:
        """Close the client connection without stopping the app-server."""
        if not self._closed:
            try:
                self._send_frame(0x8, b"")
            except OSError:
                pass
        self._closed = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    def _allocate_id(self) -> int:
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            return request_id

    def _handshake(self) -> None:
        nonce = os.urandom(16)
        key = base64.b64encode(nonce).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        response = _read_http_headers(self._socket)
        expected_accept = base64.b64encode(
            hashlib.sha1(
                key.encode("ascii") + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()
        ).decode("ascii")
        status, headers = _parse_websocket_handshake(response)
        if status != "HTTP/1.1 101 Switching Protocols":
            raise CodexOperatorProtocolError(
                "Codex app-server WebSocket upgrade was refused"
            )
        if headers.get("sec-websocket-accept") != expected_accept:
            raise CodexOperatorProtocolError(
                "Codex app-server WebSocket upgrade is invalid"
            )

    def _send_json(self, payload: Mapping[str, Any]) -> None:
        if self._closed:
            raise CodexOperatorProtocolError("Codex app-server transport is closed")
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_CODEX_RPC_MESSAGE_CHARS:
            raise CodexOperatorProtocolError("Codex app-server request is too large")
        try:
            self._send_frame(0x1, encoded)
        except OSError as exc:
            self._closed = True
            raise CodexOperatorProtocolError(
                "Codex app-server transport write failed"
            ) from exc

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        frame = _masked_websocket_frame(opcode, payload)
        with self._write_lock:
            self._socket.sendall(frame)

    def _read_frames(self) -> None:
        fragments = bytearray()
        fragment_opcode: int | None = None
        try:
            while not self._closed:
                fin, opcode, payload = _read_websocket_frame(self._socket)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                if opcode == 0x1:
                    fragments = bytearray(payload)
                    fragment_opcode = opcode
                elif opcode == 0x0 and fragment_opcode == 0x1:
                    fragments.extend(payload)
                else:
                    raise CodexOperatorProtocolError(
                        "Codex app-server WebSocket frame is unsupported"
                    )
                if len(fragments) > MAX_CODEX_RPC_MESSAGE_CHARS:
                    raise CodexOperatorProtocolError(
                        "Codex app-server response is too large"
                    )
                if not fin:
                    continue
                self._route_message(bytes(fragments))
                fragments = bytearray()
                fragment_opcode = None
        except (OSError, CodexOperatorProtocolError):
            pass
        self._closed = True

    def _route_message(self, payload: bytes) -> None:
        try:
            message = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(message, Mapping):
            return
        request_id = message.get("id")
        if request_id is not None and "method" not in message:
            with self._pending_lock:
                target = self._pending.get(request_id)
            if target is not None:
                target.put(dict(message))
                return
        try:
            self._messages.put_nowait(dict(message))
        except queue.Full as exc:
            raise CodexOperatorProtocolError(
                "Codex app-server notification queue is full"
            ) from exc


def _connect_unix(path: Path) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(str(path))
    except OSError:
        client.close()
        raise
    return client


def _read_http_headers(client: socket.socket) -> bytes:
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = client.recv(1)
        if not chunk:
            break
        response.extend(chunk)
        if len(response) > MAX_CODEX_WEBSOCKET_HEADERS_BYTES:
            raise CodexOperatorProtocolError(
                "Codex app-server WebSocket headers are too large"
            )
    if not response.endswith(b"\r\n\r\n"):
        raise CodexOperatorProtocolError(
            "Codex app-server WebSocket headers are incomplete"
        )
    return bytes(response)


def _parse_websocket_handshake(response: bytes) -> tuple[str, dict[str, str]]:
    try:
        lines = response.decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise CodexOperatorProtocolError(
            "Codex app-server WebSocket headers are invalid"
        ) from exc
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise CodexOperatorProtocolError(
                "Codex app-server WebSocket headers are invalid"
            )
        headers[name.strip().lower()] = value.strip()
    return lines[0], headers


def _masked_websocket_frame(opcode: int, payload: bytes) -> bytes:
    mask = os.urandom(4)
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, 0x80 | length))
    elif length <= 0xFFFF:
        header = bytes((0x80 | opcode, 0xFE)) + length.to_bytes(2, "big")
    else:
        header = bytes((0x80 | opcode, 0xFF)) + length.to_bytes(8, "big")
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return header + mask + masked


def _read_websocket_frame(client: socket.socket) -> tuple[bool, int, bytes]:
    first, second = _recv_exact(client, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if masked:
        raise CodexOperatorProtocolError(
            "Codex app-server sent a masked WebSocket frame"
        )
    if length == 126:
        length = int.from_bytes(_recv_exact(client, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recv_exact(client, 8), "big")
    if length > MAX_CODEX_RPC_MESSAGE_CHARS:
        raise CodexOperatorProtocolError(
            "Codex app-server WebSocket frame is too large"
        )
    if opcode >= 0x8 and (not fin or length > 125):
        raise CodexOperatorProtocolError(
            "Codex app-server WebSocket control frame is invalid"
        )
    return fin, opcode, _recv_exact(client, length)


def _recv_exact(client: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = client.recv(length - len(chunks))
        if not chunk:
            raise CodexOperatorProtocolError(
                "Codex app-server WebSocket closed unexpectedly"
            )
        chunks.extend(chunk)
    return bytes(chunks)
