"""Codex app-server protocol normalization."""

from __future__ import annotations

import json
from typing import Any, Mapping

from gigaloom.harnesses.ports import ApprovalDecision, PermissionAction
from gigaloom.structured_sessions import (
    UnsupportedSessionCapability,
)
from gigaloom.types import (
    HarnessEvent,
    HarnessEventType,
    HarnessRequest,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_PROTOCOL,
    AppServerClient,
)
from gigaloom.harnesses.builtins.codex.app_server.utils import (
    _json_hash,
    _mapping,
    _optional_text,
    _publish,
)


def _thread_identity_params(request: HarnessRequest) -> dict[str, Any]:
    return {
        "cwd": request.workspace,
        "model": request.model,
        "modelProvider": "gigaloom",
        "sandbox": "workspace-write" if request.mode == "edit" else "read-only",
        "approvalPolicy": "on-request",
    }


def _approval_contract(
    method: str,
    params: Mapping[str, Any],
) -> tuple[PermissionAction, str, dict[str, Any]]:
    """Map a reviewed Codex request family to one bounded Harness decision."""
    if method == "item/commandExecution/requestApproval":
        network = bool(_mapping(params.get("networkApprovalContext")))
        action = (
            PermissionAction.NETWORK_CONNECT
            if network
            else PermissionAction.PROCESS_SPAWN
        )
        return (
            action,
            "Codex requested approval for a command execution.",
            {
                "provider_method": method,
                "item_id": _bounded_text(params.get("itemId"), 256),
                "command": _bounded_text(params.get("command"), 2048),
                "cwd": _bounded_text(params.get("cwd"), 1024),
                "network_access": network,
            },
        )
    if method == "item/fileChange/requestApproval":
        return (
            PermissionAction.WORKSPACE_WRITE,
            "Codex requested approval for a file change.",
            {
                "provider_method": method,
                "item_id": _bounded_text(params.get("itemId"), 256),
                "reason": _bounded_text(params.get("reason"), 1024),
                "grant_root": _bounded_text(params.get("grantRoot"), 1024),
            },
        )
    if method == "item/permissions/requestApproval":
        permissions = _mapping(params.get("permissions"))
        network = bool(_mapping(permissions.get("network")).get("enabled"))
        return (
            (
                PermissionAction.NETWORK_CONNECT
                if network
                else PermissionAction.WORKSPACE_WRITE
            ),
            "Codex requested additional sandbox permissions.",
            {
                "provider_method": method,
                "item_id": _bounded_text(params.get("itemId"), 256),
                "network_access": network,
                "permission_snapshot_sha256": _json_hash(permissions),
            },
        )
    raise UnsupportedSessionCapability("Codex approval request method is unsupported")


def _provider_approval_binding(
    method: str,
    request_id: Any,
    params: Mapping[str, Any],
) -> str:
    """Build an ephemeral exact-operation binding; only its hash is persisted."""
    return json.dumps(
        {
            "protocol": APP_SERVER_PROTOCOL,
            "method": method,
            "request_id": request_id,
            "thread_id": params.get("threadId"),
            "turn_id": params.get("turnId"),
            "item_id": params.get("itemId"),
            "params_sha256": _json_hash(params),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _approval_response(
    method: str,
    decision: str,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one normalized decision into the exact Codex response shape."""
    normalized = _normalize_provider_decision(decision)
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        return {"decision": normalized}
    if method == "item/permissions/requestApproval":
        return {
            "permissions": (
                _mapping(params.get("permissions")) if normalized == "accept" else {}
            ),
            "scope": "turn",
        }
    raise UnsupportedSessionCapability("Codex approval response method is unsupported")


def _normalize_provider_decision(value: Any) -> str:
    aliases = {
        "accept": "accept",
        "allow": "accept",
        ApprovalDecision.ALLOW_ONCE.value: "accept",
        "decline": "decline",
        "deny": "decline",
        ApprovalDecision.DENY.value: "decline",
        "cancel": "cancel",
        "timeout": "decline",
    }
    try:
        return aliases[str(value)]
    except KeyError as exc:
        raise ValueError("Codex approval decision is invalid") from exc


def _bounded_text(value: Any, limit: int) -> str | None:
    text = _optional_text(value)
    return text[:limit] if text is not None else None


def _matching_turn_status(
    thread: Mapping[str, Any],
    turn_id: str | None,
) -> str | None:
    if turn_id is None:
        return None
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return None
    for item in reversed(turns):
        turn = _mapping(item)
        if str(turn.get("id") or "") != turn_id:
            continue
        status = str(turn.get("status") or "").strip().lower()
        return status or None
    return None


def _normalize_notification(
    method: str, params: Mapping[str, Any]
) -> tuple[HarnessEvent | None, str | None]:
    if method == "thread/started":
        thread = _mapping(params.get("thread"))
        return (
            HarnessEvent(
                type=HarnessEventType.EXTERNAL_THREAD_STARTED.value,
                message="Codex app-server thread started.",
                payload={"thread_id": thread.get("id") or params.get("threadId")},
            ),
            None,
        )
    if method == "thread/status/changed":
        return (
            HarnessEvent(
                type=HarnessEventType.EXTERNAL_THREAD_STATUS.value,
                message="Codex app-server thread status changed.",
                payload={"status": params.get("status")},
            ),
            None,
        )
    if method == "thread/tokenUsage/updated":
        token_usage = _mapping(params.get("tokenUsage"))
        usage = _mapping(token_usage.get("last")) or token_usage
        aliases = {
            "input_tokens": "inputTokens",
            "output_tokens": "outputTokens",
            "total_tokens": "totalTokens",
            "cached_input_tokens": "cachedInputTokens",
            "reasoning_output_tokens": "reasoningOutputTokens",
        }
        payload = {
            target: usage[source]
            for target, source in aliases.items()
            if isinstance(usage.get(source), int)
        }
        return (
            HarnessEvent(
                type=HarnessEventType.USAGE.value,
                message="Codex app-server updated token usage.",
                payload=payload,
            )
            if payload
            else None,
            None,
        )
    if method == "turn/started":
        turn = _mapping(params.get("turn"))
        return (
            HarnessEvent(
                type=HarnessEventType.EXTERNAL_TURN_STARTED.value,
                message="Codex app-server turn started.",
                payload={"turn_id": turn.get("id"), "status": turn.get("status")},
            ),
            None,
        )
    if method == "turn/plan/updated":
        plan = []
        for item in params.get("plan") or ():
            if not isinstance(item, Mapping):
                continue
            step = str(item.get("step") or "").strip()
            if not step:
                continue
            status = {
                "inProgress": "in_progress",
                "completed": "completed",
                "pending": "pending",
            }.get(str(item.get("status") or ""), "pending")
            plan.append({"step": step, "status": status})
        return (
            HarnessEvent(
                type="plan_updated",
                message="Codex app-server updated the execution plan.",
                payload={
                    "tool_call_id": f"plan:{params.get('turnId') or 'current'}",
                    "name": "update_plan",
                    "status": "running",
                    "arguments": {"plan": plan},
                },
            ),
            None,
        )
    if method == "turn/completed":
        turn = _mapping(params.get("turn"))
        return (
            HarnessEvent(
                type=HarnessEventType.EXTERNAL_TURN_COMPLETED.value,
                message="Codex app-server turn completed.",
                payload={"turn_id": turn.get("id"), "status": turn.get("status")},
            ),
            _turn_text(turn) or None,
        )
    if method == "error":
        error = params.get("error") or params.get("message") or "Codex app-server error"
        return (
            HarnessEvent(
                type=HarnessEventType.ERROR.value,
                message="Codex app-server reported an error.",
                payload={"error": error},
            ),
            None,
        )
    if method == "item/agentMessage/delta":
        delta = str(params.get("delta") or "")
        return (
            HarnessEvent(
                type=HarnessEventType.MESSAGE_DELTA.value,
                message="Codex app-server streamed assistant text.",
                payload={"delta": delta},
            )
            if delta
            else None,
            None,
        )
    if method in {
        "item/reasoning/summaryTextDelta",
        "item/reasoning/textDelta",
    }:
        delta = str(params.get("delta") or "")
        return (
            HarnessEvent(
                type=HarnessEventType.REASONING_DELTA.value,
                message="Codex app-server streamed reasoning text.",
                payload={
                    "delta": delta,
                    "item_id": params.get("itemId"),
                    "kind": (
                        "summary" if method.endswith("summaryTextDelta") else "text"
                    ),
                },
            )
            if delta
            else None,
            None,
        )
    if method not in {"item/started", "item/completed"}:
        return None, None
    item = _mapping(params.get("item"))
    item_type = str(item.get("type") or "")
    if item_type == "agentMessage":
        text = str(item.get("text") or "")
        return None, text or None
    if item_type == "fileChange" and method == "item/completed":
        return (
            HarnessEvent(
                type=HarnessEventType.FILE_CHANGED.value,
                message="Codex app-server completed a file change.",
                payload={
                    "item_id": item.get("id"),
                    "status": item.get("status"),
                    "changes": item.get("changes") or [],
                },
            ),
            None,
        )
    tool_name = _tool_name(item)
    if tool_name is None:
        return None, None
    event_type = (
        HarnessEventType.TOOL_CALL_STARTED.value
        if method == "item/started"
        else HarnessEventType.TOOL_CALL_FINISHED.value
    )
    return (
        HarnessEvent(
            type=event_type,
            message=(
                f"Codex app-server started {tool_name}."
                if method == "item/started"
                else f"Codex app-server finished {tool_name}."
            ),
            payload={
                "tool_call_id": item.get("id"),
                "name": tool_name,
                "status": item.get("status"),
                "arguments": _tool_arguments(item),
                **(
                    {"result": result}
                    if method == "item/completed"
                    and (result := _tool_result(item)) is not None
                    else {}
                ),
            },
        ),
        None,
    )


def _decline_server_request(
    client: AppServerClient,
    message: Mapping[str, Any],
    collected: list[HarnessEvent],
    request: HarnessRequest,
) -> None:
    request_id = message.get("id")
    if not isinstance(request_id, (str, int)):
        return
    method = str(message.get("method") or "")
    if method in {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }:
        client.respond(request_id, result={"decision": "decline"})
    elif method == "item/permissions/requestApproval":
        client.respond(request_id, result={"permissions": {}, "scope": "turn"})
    elif method == "mcpServer/elicitation/request":
        client.respond(request_id, result={"action": "decline"})
    elif method == "item/tool/requestUserInput":
        client.respond(request_id, result={"answers": {}})
    else:
        client.respond(
            request_id,
            error={"code": -32001, "message": "Harness client input unavailable"},
        )
    _publish(
        request,
        collected,
        HarnessEvent(
            type=HarnessEventType.WARNING.value,
            message="Codex app-server request was declined by the headless client.",
            payload={"method": method, "enforcement": "fail_closed"},
        ),
    )


def _tool_name(item: Mapping[str, Any]) -> str | None:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return "shell"
    if item_type == "mcpToolCall":
        return f"{item.get('server')}.{item.get('tool')}"
    if item_type == "dynamicToolCall":
        return str(item.get("tool") or "dynamic_tool")
    if item_type == "webSearch":
        return "web_search"
    if item_type == "collabToolCall":
        return _collab_tool(item)
    return None


def _tool_arguments(item: Mapping[str, Any]) -> Any:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return {"command": item.get("command"), "cwd": item.get("cwd")}
    if item_type == "webSearch":
        return {"query": item.get("query")}
    if item_type == "collabToolCall":
        return {
            "prompt": item.get("prompt"),
            "subagents": item.get("subagents") or _collab_agent_states(item),
        }
    return item.get("arguments") or {}


def _tool_result(item: Mapping[str, Any]) -> Any:
    for key in (
        "aggregatedOutput",
        "aggregated_output",
        "output",
        "result",
        "error",
    ):
        value = item.get(key)
        if value not in (None, "", (), [], {}):
            return value
    if str(item.get("status") or "").lower() in {"failed", "error"}:
        failure = {
            key: item[key]
            for key in (
                "exitCode",
                "exit_code",
                "stderr",
                "failureReason",
                "failure_reason",
            )
            if item.get(key) not in (None, "", (), [], {})
        }
        return failure or {"error": "Tool failed without diagnostic output."}
    return None


def _collab_tool(item: Mapping[str, Any]) -> str | None:
    value = str(item.get("tool") or "").strip()
    if not value:
        return None
    aliases = {
        "spawnAgent": "spawn_agent",
        "sendInput": "send_input",
        "resumeAgent": "resume_agent",
        "closeAgent": "close_agent",
    }
    return aliases.get(value, value)


def _collab_thread_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    values = item.get("receiverThreadIds") or item.get("receiver_thread_ids") or ()
    if not isinstance(values, (list, tuple)):
        values = (values,)
    candidates = [
        *values,
        item.get("receiverThreadId"),
        item.get("receiver_thread_id"),
        item.get("newThreadId"),
        item.get("new_thread_id"),
    ]
    return tuple(
        dict.fromkeys(
            text for value in candidates if (text := str(value or "").strip())
        )
    )


def _collab_agent_states(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    states = _mapping(item.get("agentsStates") or item.get("agents_states"))
    return [
        {
            "id": thread_id,
            "status": _mapping(state).get("status"),
            "message": _mapping(state).get("message"),
        }
        for thread_id, state in states.items()
    ]


def _turn_text(turn: Mapping[str, Any]) -> str:
    for item in reversed(tuple(turn.get("items") or ())):
        if isinstance(item, Mapping) and item.get("type") == "agentMessage":
            text = str(item.get("text") or "")
            if text:
                return text
    return ""
