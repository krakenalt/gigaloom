"""Codex dynamic-tool bridge for the bounded Thread Relay provider."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias

from gigaloom.tools import (
    THREAD_RELAY_APPROVAL_OWNER,
    RestrictedThreadRelayTools,
    thread_relay_approval_binding,
)
from gigaloom.types import HarnessRequest, redact_secrets

from gigaloom.harnesses.builtins.codex.app_server.contracts import AppServerClient


ThreadRelayToolProviderFactory: TypeAlias = Callable[
    [HarnessRequest], RestrictedThreadRelayTools | None
]
ApprovalBridge: TypeAlias = Callable[[Mapping[str, Any]], str]


def thread_relay_dynamic_tools(
    provider: RestrictedThreadRelayTools | None,
) -> tuple[dict[str, Any], ...]:
    """Project the four namespaced descriptors into Codex's public schema."""
    if provider is None:
        return ()
    tools = []
    for descriptor in provider.list_tools():
        namespace, separator, name = descriptor.id.partition(".")
        if separator != "." or namespace != "thread" or not name:
            raise ValueError("thread tool id is not a canonical namespace member")
        tools.append(
            {
                "type": "function",
                "name": name,
                "description": descriptor.description,
                "inputSchema": dict(descriptor.input_schema),
            }
        )
    return (
        {
            "type": "namespace",
            "name": "thread",
            "description": (
                "Read bounded project chats and propose user-approved cross-chat "
                "deliveries without requiring an @ mention."
            ),
            "tools": tools,
        },
    )


def handle_thread_relay_tool_call(
    client: AppServerClient,
    message: Mapping[str, Any],
    *,
    provider: RestrictedThreadRelayTools | None,
    thread_id: str | None,
    turn_id: str | None,
    approval_bridge: ApprovalBridge | None,
    timeout_seconds: float,
) -> bool:
    """Handle one exact ``item/tool/call`` request, failing closed otherwise."""
    if str(message.get("method") or "") != "item/tool/call":
        return False
    request_id = message.get("id")
    params = _mapping(message.get("params"))
    if not isinstance(request_id, (str, int)):
        return True
    if (
        provider is None
        or thread_id is None
        or turn_id is None
        or params.get("threadId") != thread_id
        or params.get("turnId") != turn_id
        or params.get("namespace") != "thread"
    ):
        _respond_failure(client, request_id, "Thread tool scope is unavailable.")
        return True
    tool = params.get("tool")
    arguments = params.get("arguments")
    if not isinstance(tool, str) or not isinstance(arguments, Mapping):
        _respond_failure(client, request_id, "Thread tool request is invalid.")
        return True
    try:
        result = dict(provider.call_tool(f"thread.{tool}", arguments))
        if result.get("requires_user_approval") is True:
            result = _complete_approved_send(
                provider,
                tool=tool,
                arguments=arguments,
                preview_result=result,
                approval_bridge=approval_bridge,
                request_id=request_id,
                params=params,
                timeout_seconds=timeout_seconds,
            )
    except Exception as exc:
        safe_detail = str(redact_secrets(str(exc)))[:400]
        _respond_failure(
            client,
            request_id,
            safe_detail or "Thread tool call failed.",
        )
        return True
    _respond(client, request_id, result, success=True)
    return True


def _complete_approved_send(
    provider: RestrictedThreadRelayTools,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    preview_result: Mapping[str, Any],
    approval_bridge: ApprovalBridge | None,
    request_id: str | int,
    params: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    if tool != "send" or approval_bridge is None:
        raise PermissionError("Thread delivery requires user approval")
    preview = _mapping(preview_result.get("preview"))
    preview_digest = str(preview.get("preview_digest") or "")
    binding = thread_relay_approval_binding(provider.scope, preview_digest)
    decision = approval_bridge(
        {
            "id": request_id,
            "method": "item/tool/call",
            "params": {
                "callId": params.get("callId"),
                "namespace": "thread",
                "threadId": params.get("threadId"),
                "tool": "send",
                "turnId": params.get("turnId"),
            },
            "timeout_seconds": timeout_seconds,
            "thread_relay_approval": {
                "approval_binding": binding,
                "enforcement_owner": THREAD_RELAY_APPROVAL_OWNER,
                "preview": preview,
                "project_id": provider.scope.project_id,
            },
        }
    )
    if decision != "accept":
        raise PermissionError("Thread delivery was not approved")
    return dict(
        provider.call_tool(
            "thread.send",
            {
                **dict(arguments),
                "preview_digest": preview_digest,
                "approval_receipt_ref": binding,
            },
        )
    )


def _respond_failure(
    client: AppServerClient,
    request_id: str | int,
    detail: str,
) -> None:
    _respond(client, request_id, {"error": detail}, success=False)


def _respond(
    client: AppServerClient,
    request_id: str | int,
    value: Mapping[str, Any],
    *,
    success: bool,
) -> None:
    client.respond(
        request_id,
        result={
            "contentItems": [
                {
                    "type": "inputText",
                    "text": json.dumps(
                        dict(value),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                }
            ],
            "success": success,
        },
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = [
    "ThreadRelayToolProviderFactory",
    "handle_thread_relay_tool_call",
    "thread_relay_dynamic_tools",
]
