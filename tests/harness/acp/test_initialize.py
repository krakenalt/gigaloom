"""ACP process admission and initialize contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
from typing import Any, Mapping

import pytest

from gigaloom.harnesses.acp import (
    AcpClientInfo,
    AcpLimits,
    AcpRouteIdentity,
    create_acp_client,
    pin_acp_process,
)
from gigaloom.harnesses.acp.errors import (
    AcpLifecycleError,
    AcpProcessError,
    AcpProtocolVersionError,
)
from gigaloom.structured_processes import StructuredTransportClosed


_CLOSED = object()
_DIGEST = "a" * 64
_ROUTE = AcpRouteIdentity("fake-agent", "fake.acp", _DIGEST)


class _InitializeTransport:
    def __init__(self, response: Mapping[str, Any]) -> None:
        self.response = dict(response)
        self.incoming: queue.Queue[Mapping[str, Any] | object] = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self._alive = False

    @property
    def runtime_id(self) -> str:
        return "fake-acp-initialize"

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self) -> None:
        self._alive = True

    def send(self, payload: Mapping[str, Any]) -> None:
        self.sent.append(dict(payload))
        if payload.get("method") == "initialize":
            self.incoming.put(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": self.response,
                }
            )

    def receive(self, timeout: float) -> Mapping[str, Any] | None:
        try:
            value = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        if value is _CLOSED:
            raise StructuredTransportClosed("closed")
        return value  # type: ignore[return-value]

    def terminate(self) -> None:
        self._alive = False

    def kill(self) -> None:
        self._alive = False

    def wait(self, timeout: float) -> int:
        del timeout
        return 0


def _executable(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-acp-agent"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    return executable


def _spec(tmp_path: Path):
    return pin_acp_process(
        (_executable(tmp_path).as_posix(), "--acp"),
        cwd=tmp_path,
        environment={
            "PATH": "/fixture/bin",
            "LANG": "C.UTF-8",
            "ACP_API_TOKEN": "do-not-inherit",
            "UNRELATED": "drop-me",
        },
        allowed_environment=frozenset({"ACP_API_TOKEN", "UNRELATED"}),
    )


def _response(protocol_version: int = 1) -> dict[str, Any]:
    return {
        "protocolVersion": protocol_version,
        "agentInfo": {"name": "fake-agent", "title": "Fake", "version": "1.2.3"},
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": False},
            "sessionCapabilities": {
                "list": {},
                "resume": {},
                "close": {},
            },
            "auth": {"logout": {}},
            "_meta": {"private": "must-not-survive"},
        },
        "authMethods": [
            {
                "id": "env",
                "name": "Environment",
                "type": "env_var",
                "vars": [
                    {"name": "FAKE_API_KEY", "description": "key", "secret": True}
                ],
            }
        ],
    }


def test_release_limits_are_explicit_and_bounded() -> None:
    limits = AcpLimits()
    assert limits.max_message_bytes == 4 * 1024 * 1024
    assert limits.max_inbound_messages == 256
    assert limits.max_outbound_messages == 256
    assert limits.max_outstanding_requests == 64


def test_process_pin_is_absolute_filters_secrets_and_detects_replacement(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    assert Path(spec.command[0]).is_absolute()
    assert spec.env == {
        "LANG": "C.UTF-8",
        "PATH": "/fixture/bin",
        "UNRELATED": "drop-me",
    }
    assert "ACP_API_TOKEN" not in spec.env
    assert len(spec.executable.fingerprint) == 64

    Path(spec.executable.path).unlink()
    replacement = Path(spec.executable.path)
    replacement.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    replacement.chmod(0o700)
    client = create_acp_client(
        spec, compatibility_profile_digest=_DIGEST, route_identity=_ROUTE
    )
    with pytest.raises(AcpProcessError, match="identity changed"):
        client.start()


def test_initialize_negotiates_v1_and_freezes_content_free_snapshot(
    tmp_path: Path,
) -> None:
    spec = _spec(tmp_path)
    transport = _InitializeTransport(_response())
    client = create_acp_client(
        spec,
        compatibility_profile_digest=_DIGEST,
        route_identity=_ROUTE,
        client_info=AcpClientInfo(version="0.7.0-alpha.1"),
        transport_factory=lambda: transport,
    )

    assert client.start() == 1
    snapshot = client.initialize()

    request = transport.sent[0]
    assert request["method"] == "initialize"
    assert request["params"]["protocolVersion"] == 1
    assert request["params"]["clientCapabilities"]["terminal"] is False
    assert snapshot.protocol_version == "1"
    assert snapshot.connection_generation == 1
    assert snapshot.process_fingerprint == spec.executable.fingerprint
    assert snapshot.agent_info is not None
    assert snapshot.agent_info.name == "fake-agent"
    assert snapshot.session_capabilities["list"] == {}
    assert snapshot.auth_capabilities["methods"] == (
        {
            "environment_variables": ("FAKE_API_KEY",),
            "id": "env",
            "kind": "env_var",
        },
    )
    encoded = json.dumps(
        {
            "agent": dict(snapshot.agent_capabilities),
            "auth": dict(snapshot.auth_capabilities),
        },
        default=dict,
    )
    assert "must-not-survive" not in encoded
    assert "do-not-inherit" not in encoded
    assert len(snapshot.snapshot_digest) == 64
    with pytest.raises(TypeError):
        snapshot.agent_capabilities["extra"] = True  # type: ignore[index]
    with pytest.raises(AcpLifecycleError, match="already initialized"):
        client.initialize()
    client.close()
    assert client.capability_snapshot is None


def test_initialize_version_mismatch_fails_closed(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    transport = _InitializeTransport(_response(protocol_version=2))
    client = create_acp_client(
        spec,
        compatibility_profile_digest=_DIGEST,
        route_identity=_ROUTE,
        transport_factory=lambda: transport,
    )
    client.start()
    with pytest.raises(AcpProtocolVersionError, match="unsupported wire version"):
        client.initialize()
    assert client.capability_snapshot is None
    assert transport.alive is False


def test_process_pin_requires_executable_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute path"):
        pin_acp_process(("fake-agent",), cwd=tmp_path, environment=dict(os.environ))
