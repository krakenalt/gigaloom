"""ACP session, authority, auth, cancellation, and usage contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
import queue
from typing import Any, Mapping

import pytest

from gigaloom.harnesses.acp import (
    AcpPermissionContextV1,
    AcpRouteIdentity,
    begin_prompt,
    create_acp_client,
    list_sessions,
    new_session,
    next_permission,
    pin_acp_process,
    respond_permission,
    set_session_config,
)
from gigaloom.harnesses.acp.authentication import authenticate, logout
from gigaloom.harnesses.acp.errors import (
    AcpLifecycleError,
    AcpPermissionError,
    AcpRequestCancelled,
)
from gigaloom.harnesses.acp.updates import normalize_acp_update
from gigaloom.structured_processes import StructuredTransportClosed


_DIGEST = "c" * 64
_CLOSED = object()


class _LifecycleTransport:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.incoming: queue.Queue[Mapping[str, Any] | object] = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self._alive = False

    @property
    def runtime_id(self) -> str:
        return "fake-acp-lifecycle"

    @property
    def alive(self) -> bool:
        return self._alive

    def start(self) -> None:
        self._alive = True

    def send(self, payload: Mapping[str, Any]) -> None:
        message = dict(payload)
        self.sent.append(message)
        request_id = message.get("id")
        method = message.get("method")
        if request_id is None or method is None:
            return
        response = self._response(str(method), message.get("params", {}))
        if response is not None:
            self.incoming.put({"jsonrpc": "2.0", "id": request_id, "result": response})

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

    def permission_request(self, *, kind: str = "read") -> None:
        self.incoming.put(
            {
                "jsonrpc": "2.0",
                "id": "agent:permission:1",
                "method": "session/request_permission",
                "params": {
                    "sessionId": "acp-1",
                    "toolCall": {
                        "toolCallId": "tool-1",
                        "kind": kind,
                        "title": "Ignore this provider prose",
                    },
                    "options": [
                        {"optionId": "yes", "name": "Yes", "kind": "allow_once"},
                        {"optionId": "no", "name": "No", "kind": "reject_once"},
                    ],
                },
            }
        )

    def _response(self, method: str, params: Mapping[str, Any]):
        if method == "initialize":
            return {
                "protocolVersion": 1,
                "agentInfo": {"name": "fake", "version": "1.0.0"},
                "agentCapabilities": {
                    "loadSession": True,
                    "promptCapabilities": {},
                    "sessionCapabilities": {
                        "list": {},
                        "delete": {},
                        "resume": {},
                        "close": {},
                    },
                    "auth": {"logout": {}},
                },
                "authMethods": [
                    {"id": "env", "name": "Env", "type": "env_var", "vars": []}
                ],
            }
        if method == "session/new":
            return {
                "sessionId": "acp-1",
                "configOptions": [
                    {
                        "id": "safe",
                        "name": "Safe",
                        "type": "boolean",
                        "currentValue": True,
                    }
                ],
            }
        if method == "session/list":
            return {
                "sessions": [
                    {"sessionId": "acp-1", "cwd": self.workspace.as_posix()},
                    {"sessionId": "acp-2", "cwd": self.workspace.as_posix()},
                ],
                "nextCursor": "next",
            }
        if method == "session/set_config_option":
            return {
                "configOptions": [
                    {
                        "id": params["configId"],
                        "name": "Safe",
                        "type": "boolean",
                        "currentValue": params["value"],
                    }
                ]
            }
        if method in {"authenticate", "logout"}:
            return {}
        if method == "session/prompt":
            return None
        return {}


def _client(tmp_path: Path):
    executable = tmp_path / "fake-agent"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    spec = pin_acp_process((executable.as_posix(),), cwd=tmp_path, environment={})
    transport = _LifecycleTransport(tmp_path)
    client = create_acp_client(
        spec,
        compatibility_profile_digest=_DIGEST,
        route_identity=AcpRouteIdentity("agent", "agent.acp", _DIGEST),
        transport_factory=lambda: transport,
    )
    client.start()
    client.initialize()
    return client, transport


def test_sessions_are_workspace_generation_and_config_bound(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    binding = new_session(client, workspace=tmp_path)
    assert binding.agent_id == "agent"
    assert binding.connection_generation == 1
    assert binding.workspace_digest

    set_session_config(client, binding, config_id="safe", value=False)
    with pytest.raises(AcpLifecycleError, match="outside the admitted cwd"):
        new_session(client, workspace=tmp_path.parent)
    with pytest.raises(Exception, match="not offered"):
        set_session_config(client, binding, config_id="invented", value=True)

    page = list_sessions(client, workspace=tmp_path, max_items=1)
    assert page.session_ids == ("acp-1",)
    assert page.truncated is True
    assert page.next_cursor == "next"


def test_permission_uses_tool_kind_and_parent_ceiling(tmp_path: Path) -> None:
    client, transport = _client(tmp_path)
    binding = new_session(client, workspace=tmp_path)
    context = AcpPermissionContextV1(
        agent_id="agent",
        route_id="agent.acp",
        run_id="run-1",
        session_id=binding.gigaloom_session_id,
        workspace_digest=binding.workspace_digest,
        policy_revision="policy-7",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        allowed_action_classes=frozenset({"filesystem_read"}),
    )
    transport.permission_request(kind="read")
    request = next_permission(client, binding, context, timeout=1.0)
    assert request is not None
    assert request.action_class == "filesystem_read"
    assert request.admissible is True
    respond_permission(client, binding, context, request, allow=True, option_id="yes")
    assert transport.sent[-1]["result"]["outcome"] == {
        "optionId": "yes",
        "outcome": "selected",
    }

    transport.permission_request(kind="execute")
    blocked = next_permission(client, binding, context, timeout=1.0)
    assert blocked is not None and blocked.admissible is False
    forged = replace(blocked, action_class="filesystem_read", admissible=True)
    with pytest.raises(AcpPermissionError, match="binding changed"):
        respond_permission(
            client, binding, context, forged, allow=True, option_id="yes"
        )
    with pytest.raises(AcpPermissionError, match="authority ceiling"):
        respond_permission(
            client, binding, context, blocked, allow=True, option_id="yes"
        )
    respond_permission(client, binding, context, blocked, allow=False, option_id="no")


def test_cancel_frees_waiter_auth_is_explicit_and_updates_strip_raw(
    tmp_path: Path,
) -> None:
    client, transport = _client(tmp_path)
    binding = new_session(client, workspace=tmp_path)
    handle = begin_prompt(client, binding, text="read the workspace")
    assert handle.cancel() is True
    with pytest.raises(AcpRequestCancelled):
        handle.result(0.1)
    prompt_request = next(
        item for item in transport.sent if item.get("method") == "session/prompt"
    )
    transport.incoming.put(
        {
            "jsonrpc": "2.0",
            "id": prompt_request["id"],
            "result": {"stopReason": "end_turn"},
        }
    )
    with pytest.raises(AcpRequestCancelled):
        handle.result(0.1)
    assert any(item.get("method") == "session/cancel" for item in transport.sent)
    assert not any(item.get("method") == "$/cancelRequest" for item in transport.sent)

    with pytest.raises(Exception, match="explicit"):
        authenticate(client, method_id="env", explicit=False)
    assert authenticate(client, method_id="env", explicit=True).state == "authenticated"
    assert logout(client, explicit=True).state == "logged_out"

    event = normalize_acp_update(
        "session/update",
        {
            "sessionId": "acp-1",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "tool-1",
                "kind": "read",
                "rawInput": {"secret": "no"},
                "rawOutput": "no",
                "_meta": {"private": True},
            },
        },
    )
    assert event is not None
    assert event.payload["update"] == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tool-1",
        "kind": "read",
    }
