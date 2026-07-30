"""Codex app-server stdio JSON-RPC process lifecycle."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from typing import Any, Mapping

from gigaloom.types import (
    redact_secrets,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_STDERR_CHARS,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerProtocolError,
)


class _StdioJsonRpcClient:
    """Own one app-server stdio process and demultiplex JSON-RPC messages."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        env: Mapping[str, str],
        cwd: str,
        runtime_id: str,
    ) -> None:
        self.runtime_id = runtime_id
        self.command = command
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=dict(env),
            cwd=cwd,
            start_new_session=True,
            bufsize=1,
        )
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[str | int, queue.Queue[Mapping[str, Any]]] = {}
        self._messages: queue.Queue[Mapping[str, Any]] = queue.Queue()
        self._next_id = 1
        self._stderr: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "gigaloom",
                    "title": "gpt2giga Harness",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": False},
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        self._send({"method": "initialized"})

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def request(
        self, method: str, params: Mapping[str, Any], *, timeout: float
    ) -> Mapping[str, Any]:
        request_id = self._allocate_id()
        response_queue: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        self._send({"id": request_id, "method": method, "params": dict(params)})
        try:
            response = response_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise AppServerProtocolError(
                f"Codex app-server request timed out: {method}"
            ) from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in response:
            raise AppServerProtocolError(
                f"Codex app-server {method} failed: "
                f"{redact_secrets(response.get('error'))}"
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise AppServerProtocolError(
                f"Codex app-server {method} returned an invalid result"
            )
        return dict(result)

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None:
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
        payload: dict[str, Any] = {"id": request_id}
        if error is not None:
            payload["error"] = dict(error)
        else:
            payload["result"] = dict(result or {})
        self._send(payload)

    def close(self) -> None:
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
            raise AppServerProtocolError("Codex app-server transport is closed")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            self._process.stdin.write(line)
            self._process.stdin.flush()

    def _read_stdout(self) -> None:
        if self._process.stdout is None:
            return
        for line in self._process.stdout:
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
            self._messages.put(dict(payload))

    def _read_stderr(self) -> None:
        if self._process.stderr is None:
            return
        for line in self._process.stderr:
            self._stderr.append(line)
            total = sum(len(item) for item in self._stderr)
            while self._stderr and total > APP_SERVER_STDERR_CHARS:
                total -= len(self._stderr.pop(0))
