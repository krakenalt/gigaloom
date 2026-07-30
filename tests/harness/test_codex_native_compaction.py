"""Native Codex compaction request and lifecycle contracts."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import socket
import threading

from gigaloom.native.codex_operator import (
    CODEX_MAXIMUM_VERSION_EXCLUSIVE,
    CODEX_MINIMUM_VERSION,
    CODEX_SCHEMA_BUNDLE_SHA256,
    CodexCapabilityState,
    CodexCompatibilitySnapshot,
    CodexCompactionStatus,
    CodexNativeCompactionService,
    CodexUnixWebSocketJsonRpcClient,
)


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)


class _Client:
    def __init__(self, messages, *, resumed_thread="thread-fixture") -> None:
        self.messages = list(messages)
        self.resumed_thread = resumed_thread
        self.requests = []
        self.responses = []

    def request(self, method, params, *, timeout):
        self.requests.append((method, dict(params), timeout))
        if method == "thread/resume":
            return {"thread": {"id": self.resumed_thread}}
        if method == "thread/compact/start":
            return {}
        raise AssertionError(method)

    def next_message(self, *, timeout):
        del timeout
        return self.messages.pop(0) if self.messages else None

    def respond(self, request_id, *, result=None, error=None):
        self.responses.append((request_id, result, error))


def _compatibility(structured=True):
    status = (
        CodexCapabilityState.SUPPORTED
        if structured
        else CodexCapabilityState.NATIVE_ONLY
    )
    return CodexCompatibilitySnapshot(
        status=status,
        executable_version="codex-cli 0.144.5",
        parsed_version="0.144.5",
        minimum_version=CODEX_MINIMUM_VERSION,
        maximum_version_exclusive=CODEX_MAXIMUM_VERSION_EXCLUSIVE,
        schema_bundle_sha256=CODEX_SCHEMA_BUNDLE_SHA256,
        capabilities={"thread_compact": status},
        transport="unix" if structured else None,
        reason_code="fixture",
    )


def _lifecycle():
    return [
        {"method": "future/additive", "params": {"value": "ignored"}},
        {
            "id": 99,
            "method": "future/serverRequest",
            "params": {"secret": "not-projected"},
        },
        {
            "method": "item/started",
            "params": {
                "threadId": "other-thread",
                "turnId": "turn-other",
                "item": {"id": "item-other", "type": "contextCompaction"},
            },
        },
        {
            "method": "item/started",
            "params": {
                "threadId": "thread-fixture",
                "turnId": "turn-compact",
                "item": {"id": "item-compact", "type": "contextCompaction"},
            },
        },
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-fixture",
                "turnId": "turn-compact",
                "item": {"id": "item-compact", "type": "contextCompaction"},
            },
        },
    ]


def test_compaction_requires_upstream_started_and_completed_before_hook():
    client = _Client(_lifecycle())
    boundaries = []
    service = CodexNativeCompactionService(
        _compatibility(),
        now=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    outcome = service.compact(
        client,
        binding_id="codex-binding",
        native_thread_id="thread-fixture",
        cwd="/workspace",
        previous_manifest_digest="a" * 64,
        manifest_revision_hook=boundaries.append,
    )

    assert outcome.status is CodexCompactionStatus.COMPLETED
    assert outcome.boundary == boundaries[0]
    assert outcome.boundary is not None
    assert outcome.boundary.upstream_turn_id == "turn-compact"
    assert outcome.boundary.upstream_item_id == "item-compact"
    assert outcome.boundary.previous_manifest_digest == "a" * 64
    assert outcome.boundary.native_thread_digest != "thread-fixture"
    assert outcome.boundary.omissions == (
        "complete_native_context",
        "hidden_reasoning",
        "provider_owned_state",
    )
    assert [request[0] for request in client.requests] == [
        "thread/resume",
        "thread/compact/start",
    ]
    assert client.responses[0][0] == 99
    assert client.responses[0][2]["code"] == -32601


def test_incomplete_or_changed_lifecycle_never_calls_manifest_hook():
    service = CodexNativeCompactionService(
        _compatibility(),
        monotonic=lambda: 0.0,
    )
    boundaries = []
    incomplete = _Client(_lifecycle()[:-1])
    changed = _Client(_lifecycle(), resumed_thread="different")

    incomplete_outcome = service.compact(
        incomplete,
        binding_id="binding",
        native_thread_id="thread-fixture",
        cwd="/workspace",
        previous_manifest_digest="b" * 64,
        manifest_revision_hook=boundaries.append,
        timeout_seconds=0.01,
    )
    changed_outcome = service.compact(
        changed,
        binding_id="binding",
        native_thread_id="thread-fixture",
        cwd="/workspace",
        previous_manifest_digest="b" * 64,
        manifest_revision_hook=boundaries.append,
    )

    assert incomplete_outcome.status is CodexCompactionStatus.FAILED
    assert changed_outcome.reason_code == "resumed_thread_identity_changed"
    assert boundaries == []


def test_native_only_fallback_does_not_simulate_success():
    client = _Client(_lifecycle())
    hooks = []

    outcome = CodexNativeCompactionService(_compatibility(False)).compact(
        client,
        binding_id="binding",
        native_thread_id="thread-fixture",
        cwd="/workspace",
        previous_manifest_digest="c" * 64,
        manifest_revision_hook=hooks.append,
    )

    assert outcome.status is CodexCompactionStatus.NATIVE_ONLY
    assert outcome.message == "Use native /compact inside the Codex TUI."
    assert client.requests == []
    assert hooks == []


def test_manifest_hook_failure_is_not_reported_as_compaction_success():
    outcome = CodexNativeCompactionService(
        _compatibility(),
        now=lambda: NOW,
        monotonic=lambda: 0.0,
    ).compact(
        _Client(_lifecycle()),
        binding_id="binding",
        native_thread_id="thread-fixture",
        cwd="/workspace",
        previous_manifest_digest="d" * 64,
        manifest_revision_hook=lambda _: (_ for _ in ()).throw(RuntimeError("fixture")),
    )

    assert outcome.status is CodexCompactionStatus.FAILED
    assert outcome.reason_code == "manifest_revision_hook_failed"


def test_unix_websocket_client_initializes_and_demultiplexes_messages():
    client_socket, server_socket = socket.socketpair()
    received = []

    def server():
        headers = _read_until(server_socket, b"\r\n\r\n").decode("ascii")
        key = next(
            line.partition(":")[2].strip()
            for line in headers.split("\r\n")
            if line.lower().startswith("sec-websocket-key:")
        )
        accept = base64.b64encode(
            hashlib.sha1(
                key.encode() + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest()
        ).decode()
        server_socket.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode()
        )
        try:
            for _ in range(3):
                request = json.loads(_read_client_text_frame(server_socket))
                received.append(request)
                if "id" in request:
                    _send_server_text_frame(
                        server_socket,
                        {"id": request["id"], "result": {}},
                    )
                if request.get("method") == "thread/compact/start":
                    _send_server_text_frame(
                        server_socket,
                        {
                            "method": "item/started",
                            "params": {"threadId": "t"},
                        },
                    )
        finally:
            server_socket.close()

    thread = threading.Thread(target=server)
    thread.start()
    client = CodexUnixWebSocketJsonRpcClient(
        socket_path="/tmp/private-codex.sock",
        runtime_id="fixture-runtime",
        connect=lambda _: client_socket,
    )
    result = client.request(
        "thread/compact/start",
        {"threadId": "t"},
        timeout=1.0,
    )
    notification = client.next_message(timeout=1.0)
    client.close()
    thread.join(timeout=1.0)

    assert result == {}
    assert [message["method"] for message in received] == [
        "initialize",
        "initialized",
        "thread/compact/start",
    ]
    assert notification == {
        "method": "item/started",
        "params": {"threadId": "t"},
    }


def _read_until(client, marker):
    result = bytearray()
    while marker not in result:
        result.extend(client.recv(1024))
    return bytes(result)


def _recv_exact(client, length):
    result = bytearray()
    while len(result) < length:
        result.extend(client.recv(length - len(result)))
    return bytes(result)


def _read_client_text_frame(client):
    first, second = _recv_exact(client, 2)
    assert first == 0x81
    assert second & 0x80
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_recv_exact(client, 2), "big")
    elif length == 127:
        length = int.from_bytes(_recv_exact(client, 8), "big")
    mask = _recv_exact(client, 4)
    payload = _recv_exact(client, length)
    return bytes(
        value ^ mask[index % 4] for index, value in enumerate(payload)
    ).decode()


def _send_server_text_frame(client, payload):
    body = json.dumps(payload).encode()
    assert len(body) < 126
    client.sendall(bytes((0x81, len(body))) + body)
