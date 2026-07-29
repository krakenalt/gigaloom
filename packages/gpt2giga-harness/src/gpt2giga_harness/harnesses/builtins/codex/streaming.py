"""Codex CLI harness for running Codex through local gpt2giga."""

from __future__ import annotations

from importlib import metadata
from typing import Any, Mapping

from gpt2giga_harness.harnesses.agent_cli import (
    StreamTerminalOutcome,
    message_delta_event,
    stream_terminal_failure,
    tool_call_event,
    usage_event,
)
from gpt2giga_harness.types import (
    HarnessEvent,
)

MODE_TO_SANDBOX = {
    "plan": "read-only",
    "read": "read-only",
    "edit": "workspace-write",
}
GPT2GIGA_ATTACHMENT_IDS_HEADER = "x-gpt2giga-attachment-ids"


class _CodexStreamParser:
    """Normalize Codex CLI JSONL events without repeating final message text."""

    def __init__(self) -> None:
        self._item_text: dict[str, str] = {}
        self.terminal_outcome: StreamTerminalOutcome | None = None
        self.recognized_payloads = 0

    def __call__(self, payload: Mapping[str, Any]) -> tuple[HarnessEvent, ...]:
        events: list[HarnessEvent] = []
        event_type = str(payload.get("type") or "")
        if event_type in {
            "thread.started",
            "turn.started",
            "turn.completed",
            "turn.failed",
            "item.started",
            "item.updated",
            "item.completed",
            "item.failed",
            "error",
        }:
            self.recognized_payloads += 1
        item = _mapping(payload.get("item"))
        item_type = str(item.get("type") or "")
        item_id = str(item.get("id") or payload.get("item_id") or item_type or "item")

        if item_type == "agent_message":
            message_event = self._message_event(payload, item, item_id)
            if message_event is not None:
                events.append(message_event)
        elif _is_codex_tool_item(item_type):
            tool_event = _codex_tool_event(event_type, item, item_id)
            if tool_event is not None:
                events.append(tool_event)
            artifact_event = _codex_artifact_event(event_type, item, item_id)
            if artifact_event is not None:
                events.append(artifact_event)

        normalized_usage = usage_event(payload.get("usage"))
        if normalized_usage is not None:
            events.append(normalized_usage)

        if event_type in {"error", "turn.failed", "item.failed"}:
            error = (
                payload.get("message")
                or payload.get("error")
                or item.get("error")
                or item.get("message")
            )
            failure = stream_terminal_failure(
                error,
                fallback={
                    "error": "Codex CLI reported an error",
                    "turn.failed": "Codex CLI turn failed",
                    "item.failed": "Codex CLI item failed",
                }[event_type],
            )
            if self.terminal_outcome is None:
                self.terminal_outcome = failure
            events.append(
                HarnessEvent(
                    type="stderr_delta",
                    message="Codex CLI reported an error.",
                    payload={"delta": failure.error or "Codex CLI failed"},
                )
            )
        return tuple(events)

    def _message_event(
        self,
        payload: Mapping[str, Any],
        item: Mapping[str, Any],
        item_id: str,
    ) -> HarnessEvent | None:
        explicit_delta = payload.get("delta")
        if isinstance(explicit_delta, str) and explicit_delta:
            previous = self._item_text.get(item_id, "")
            self._item_text[item_id] = previous + explicit_delta
            return message_delta_event(explicit_delta)

        text = item.get("text") or item.get("content")
        if not isinstance(text, str) or not text:
            return None
        previous = self._item_text.get(item_id, "")
        if text == previous:
            return None
        if previous and text.startswith(previous):
            delta = text[len(previous) :]
        elif previous:
            return None
        else:
            delta = text
        self._item_text[item_id] = text
        return message_delta_event(delta)


def _is_codex_tool_item(item_type: str) -> bool:
    return item_type in {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "todo_list",
        "web_search",
        "dynamic_tool_call",
        "test_result",
    } or item_type.endswith("_tool_call")


def _codex_tool_event(
    event_type: str,
    item: Mapping[str, Any],
    item_id: str,
) -> HarnessEvent | None:
    item_type = str(item.get("type") or "tool")
    name = _codex_tool_name(item_type, item)
    arguments = _codex_tool_arguments(item_type, item)
    status = item.get("status")
    if event_type == "item.started":
        return tool_call_event(
            "tool_call_started",
            tool_call_id=item_id,
            name=name,
            arguments=arguments,
            status=status or "running",
        )
    if event_type == "item.updated":
        if item_type == "todo_list":
            return tool_call_event(
                "tool_call_delta",
                tool_call_id=item_id,
                name=name,
                arguments=arguments,
                status=status or "running",
                arguments_are_complete=True,
            )
        arguments_delta = _first_present(item, "arguments_delta", "input_delta")
        result_delta = _first_present(item, "output_delta", "delta")
        if arguments_delta is None and result_delta is None and status is None:
            return None
        return tool_call_event(
            "tool_call_delta",
            tool_call_id=item_id,
            name=name,
            arguments=arguments_delta,
            result=result_delta,
            status=status,
        )
    if event_type in {"item.completed", "item.failed"}:
        result = _codex_tool_result(item, failed=event_type == "item.failed")
        return tool_call_event(
            "tool_call_finished",
            tool_call_id=item_id,
            name=name,
            arguments=arguments,
            result=result,
            status=status or ("failed" if event_type == "item.failed" else "completed"),
        )
    return None


def _codex_tool_result(item: Mapping[str, Any], *, failed: bool) -> Any:
    result = _first_present(
        item,
        "aggregated_output",
        "output",
        "result",
        "error",
        "stderr",
        "message",
    )
    if result not in (None, "", (), [], {}):
        return result
    if not failed and str(item.get("status") or "").lower() not in {
        "failed",
        "error",
    }:
        return result
    exit_code = item.get("exit_code")
    if exit_code is not None:
        return f"Command exited with code {exit_code} and produced no output."
    return "Codex marked this tool call as failed without an error message."


def _codex_artifact_event(
    event_type: str,
    item: Mapping[str, Any],
    item_id: str,
) -> HarnessEvent | None:
    """Emit stable artifacts only for explicit structured Codex item kinds."""
    if event_type not in {"item.completed", "item.failed"}:
        return None
    item_type = str(item.get("type") or "")
    status = str(
        item.get("status") or ("failed" if event_type == "item.failed" else "completed")
    )
    if item_type == "command_execution":
        payload = {
            "artifact_id": item_id,
            "artifact_type": "command",
            "command": item.get("command"),
            "exit_code": item.get("exit_code"),
            "status": status,
        }
        return HarnessEvent(
            type="command_completed",
            message="Command execution completed.",
            payload={key: value for key, value in payload.items() if value is not None},
        )
    if item_type == "file_change":
        payload = {
            "artifact_id": item_id,
            "artifact_type": "file_change",
            "changes": item.get("changes"),
            "status": status,
        }
        return HarnessEvent(
            type="file_changed",
            message="File change completed.",
            payload={key: value for key, value in payload.items() if value is not None},
        )
    if item_type == "test_result":
        payload = {
            "artifact_id": item_id,
            "artifact_type": "test",
            "name": item.get("name"),
            "status": status,
        }
        return HarnessEvent(
            type="test_completed",
            message="Test execution completed.",
            payload={key: value for key, value in payload.items() if value is not None},
        )
    return None


def _codex_tool_name(item_type: str, item: Mapping[str, Any]) -> str:
    explicit = item.get("name") or item.get("tool") or item.get("tool_name")
    if explicit:
        return str(explicit)
    if item_type == "command_execution":
        return "shell"
    if item_type == "todo_list":
        return "update_plan"
    return item_type or "tool"


def _codex_tool_arguments(item_type: str, item: Mapping[str, Any]) -> Any:
    if item_type == "todo_list":
        todo_items = item.get("items")
        if not isinstance(todo_items, list):
            return {"plan": []}
        first_incomplete = next(
            (
                index
                for index, todo in enumerate(todo_items)
                if isinstance(todo, Mapping) and not bool(todo.get("completed"))
            ),
            None,
        )
        plan = []
        for index, todo in enumerate(todo_items):
            if not isinstance(todo, Mapping):
                continue
            text = todo.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            status = (
                "completed"
                if bool(todo.get("completed"))
                else "in_progress"
                if index == first_incomplete
                else "pending"
            )
            plan.append({"step": text.strip(), "status": status})
        return {"plan": plan}
    if item_type == "collab_tool_call":
        receiver_ids = item.get("receiver_thread_ids") or ()
        if not isinstance(receiver_ids, (list, tuple)):
            receiver_ids = (receiver_ids,)
        states = _mapping(item.get("agents_states"))
        return {
            "prompt": item.get("prompt"),
            "subagents": [
                {
                    "id": thread_id,
                    "name": str(thread_id)[:8],
                    "status": _mapping(states.get(thread_id)).get("status"),
                    "message": _mapping(states.get(thread_id)).get("message"),
                }
                for thread_id in receiver_ids
                if str(thread_id or "").strip()
            ],
        }
    return _first_present(
        item,
        "arguments",
        "input",
        "command",
        "query",
        "changes",
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_present(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if value.get(key) is not None:
            return value[key]
    return None


def _adapter_version() -> str:
    try:
        value = metadata.version("gigaloom")
    except metadata.PackageNotFoundError:
        value = "unknown"
    return str(value).strip() or "unknown"
