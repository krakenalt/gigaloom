"""Managed terminal browser attach API."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from queue import Empty, Queue

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gigaloom.config import HarnessConfig
from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalIdentity,
    TerminalLiveness,
    TerminalLivenessKind,
    TerminalRecord,
    TerminalState,
    TerminalWebSocketCloseCode,
)
from gigaloom.native.terminal.control_bridge import TmuxControlReadTimeout
from gigaloom.ui.app import create_app
from gigaloom.ui.services.operator_terminal import ManagedTerminalBrowserOwner


ORIGIN = "http://testserver"
OWNER = "local_operator"
WORKSPACE = "workspace-1"
SESSION = "session-1"
DIGEST = "a" * 64


class _Client:
    def __init__(self) -> None:
        self.outputs: Queue[bytes] = Queue()
        self.inputs: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.close_calls = 0

    def read_line(self) -> bytes:
        try:
            return self.outputs.get(timeout=0.05)
        except Empty as exc:
            raise TmuxControlReadTimeout("quiet terminal") from exc

    def send_input(self, data: bytes) -> None:
        self.inputs.append(data)

    def resize(self, *, rows: int, columns: int) -> None:
        self.resizes.append((rows, columns))

    def close(self) -> None:
        self.close_calls += 1


class _Backend:
    def __init__(self) -> None:
        self.client = _Client()
        self.calls: list[tuple[str, str]] = []

    def capture_seed(self, terminal_id: str) -> bytes:
        self.calls.append(("capture", terminal_id))
        return b"seed"

    def open_control_mode(self, terminal_id: str) -> _Client:
        self.calls.append(("open", terminal_id))
        return self.client

    def liveness(self, terminal_id: str) -> TerminalLiveness:
        return TerminalLiveness(TerminalLivenessKind.LIVE)


def test_terminal_description_is_revision_bound_and_has_no_backend_target(
    tmp_path,
) -> None:
    client, _, record = _client(tmp_path)

    response = client.get(
        f"/api/operator/terminals/{record.id}/attach",
        params={
            "workspace_id": WORKSPACE,
            "session_id": SESSION,
            "revision": record.revision,
        },
    )

    assert response.status_code == 200
    projection = response.json()["terminal"]
    assert projection["attachable"] is True
    assert projection["transport"]["output"] == "binary"
    assert projection["reconnect"]["strategy"] == "reauthorize_and_resnapshot"
    assert projection["close_reasons"]["4400"] == "protocol_error"
    assert projection["close_reasons"]["4429"] == "backpressure"
    assert f"revision={record.revision}" in projection["websocket_path"]
    serialized = json.dumps(projection, sort_keys=True)
    for forbidden in (
        "tmux_socket_path",
        "private_socket_path",
        "native_home_path",
        "terminal_output",
        "credential",
    ):
        assert forbidden not in serialized
    assert (
        client.get(
            f"/api/operator/terminals/{record.id}/attach",
            params={
                "workspace_id": "other-workspace",
                "session_id": SESSION,
                "revision": record.revision,
            },
        ).status_code
        == 403
    )


def test_terminal_websocket_streams_binary_and_revision_bound_resize(
    tmp_path,
) -> None:
    client, backend, record = _client(tmp_path)
    path = (
        f"/api/operator/terminals/{record.id}/attach/ws"
        f"?workspace_id={WORKSPACE}&session_id={SESSION}"
        f"&revision={record.revision}"
    )

    with client.websocket_connect(path, headers={"origin": ORIGIN}) as socket:
        assert socket.receive_bytes() == b"seed"
        socket.send_bytes(b"\x00input")
        socket.send_text(
            json.dumps(
                {
                    "type": "resize",
                    "rows": 40,
                    "columns": 120,
                    "revision": record.revision,
                }
            )
        )
        backend.client.outputs.put(b"%output %1 echo\\012\n")
        assert socket.receive_bytes() == b"echo\n"

    assert b"".join(backend.client.inputs) == b"\x00input"
    assert backend.client.resizes == [(40, 120)]
    assert backend.calls == [("capture", record.id), ("open", record.id)]
    assert backend.client.close_calls == 1


def test_terminal_websocket_rejects_missing_origin_before_backend_resolution(
    tmp_path,
) -> None:
    client, backend, record = _client(tmp_path)
    path = (
        f"/api/operator/terminals/{record.id}/attach/ws"
        f"?workspace_id={WORKSPACE}&session_id={SESSION}"
        f"&revision={record.revision}"
    )

    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect(path):
            pass

    assert denied.value.code == TerminalWebSocketCloseCode.FORBIDDEN
    assert backend.calls == []


def _client(tmp_path) -> tuple[TestClient, _Backend, TerminalRecord]:
    registry = ManagedTerminalRegistry(
        now=lambda: datetime(2026, 7, 31, tzinfo=timezone.utc),
        id_factory=lambda: "terminal-1",
    )
    record = registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)
    backend = _Backend()
    owner = ManagedTerminalBrowserOwner(
        registry,
        backend,
        allowed_origins=(ORIGIN,),
    )
    app = create_app(
        HarnessConfig(data_dir=tmp_path),
        terminal_browser_owner=owner,
    )
    return TestClient(app), backend, record


def _identity() -> TerminalIdentity:
    return TerminalIdentity(
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        session_id=SESSION,
        terminal_name="codex",
        session_key="session-key",
        native_harness_id="codex-cli",
        command_digest=DIGEST,
        cwd_digest=DIGEST,
        executable_path_digest=DIGEST,
        executable_version="0.144.5",
    )
