"""Codex app-server rollout and multi-agent projections."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from gigaloom.types import (
    HarnessEvent,
    HarnessEventType,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerClient,
    AppServerProtocolError,
)
from gigaloom.harnesses.builtins.codex.app_server.protocol import (
    _collab_agent_states,
    _collab_thread_ids,
    _tool_arguments,
    _tool_name,
    _tool_result,
)
from gigaloom.harnesses.builtins.codex.app_server.utils import _mapping


def _read_collab_subagents(
    client: AppServerClient,
    item: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    states = {str(entry.get("id")): entry for entry in _collab_agent_states(item)}
    snapshots: list[dict[str, Any]] = []
    for thread_id in _collab_thread_ids(item):
        try:
            response = client.request(
                "thread/read",
                {"threadId": thread_id, "includeTurns": True},
                timeout=APP_SERVER_TIMEOUT_SECONDS,
            )
        except (AppServerProtocolError, OSError, RuntimeError, ValueError):
            thread = {}
        else:
            thread = _mapping(response.get("thread"))
        state = states.get(thread_id, {})
        snapshots.append(
            {
                "id": thread_id,
                "name": (
                    thread.get("agentNickname")
                    or thread.get("agent_nickname")
                    or thread_id[:8]
                ),
                "role": thread.get("agentRole") or thread.get("agent_role"),
                "status": state.get("status")
                or _mapping(thread.get("status")).get("type"),
                "message": state.get("message"),
                "prompt": item.get("prompt"),
                "turns": thread.get("turns") or [],
            }
        )
    return tuple(snapshots)


def _public_collab_subagent(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: snapshot.get(key)
        for key in ("id", "name", "role", "status", "message", "prompt")
        if snapshot.get(key) is not None
    }


def _collab_child_tool_events(
    snapshots: tuple[dict[str, Any], ...],
    *,
    subagent_parents: Mapping[str, str],
    seen: set[tuple[str, str, str]],
) -> tuple[HarnessEvent, ...]:
    events: list[HarnessEvent] = []
    for snapshot in snapshots:
        child_id = str(snapshot.get("id") or "")
        parent_id = subagent_parents.get(child_id)
        if not child_id or not parent_id:
            continue
        for turn in snapshot.get("turns") or ():
            if not isinstance(turn, Mapping):
                continue
            for item in turn.get("items") or ():
                if not isinstance(item, Mapping):
                    continue
                name = _tool_name(item)
                item_id = str(item.get("id") or "").strip()
                if name is None or not item_id:
                    continue
                status = str(item.get("status") or "completed")
                event_type = (
                    HarnessEventType.TOOL_CALL_STARTED.value
                    if status in {"inProgress", "in_progress", "running"}
                    else HarnessEventType.TOOL_CALL_FINISHED.value
                )
                identity = (child_id, item_id, event_type)
                if identity in seen:
                    continue
                seen.add(identity)
                payload = {
                    "tool_call_id": f"{child_id}:{item_id}",
                    "parent_tool_call_id": parent_id,
                    "name": name,
                    "status": status,
                    "arguments": _tool_arguments(item),
                    "source": "codex-app-server-subagent",
                    "subagent_id": child_id,
                    "subagent_name": snapshot.get("name"),
                    "subagent_role": snapshot.get("role"),
                    "subagent_description": snapshot.get("prompt"),
                }
                result = _tool_result(item)
                if (
                    event_type == HarnessEventType.TOOL_CALL_FINISHED.value
                    and result is not None
                ):
                    payload["result"] = result
                events.append(
                    HarnessEvent(
                        type=event_type,
                        message=f"Codex subagent {snapshot.get('name')} ran {name}.",
                        payload=payload,
                    )
                )
    return tuple(events)


def _rollout_multi_agent_events(
    client: AppServerClient,
    *,
    tail: _RolloutMultiAgentTail,
    seen: set[tuple[str, str]],
    child_seen: set[tuple[str, str, str]],
) -> tuple[HarnessEvent, ...]:
    """Live-tail multi-agent calls omitted from Codex app-server notifications."""
    events: list[HarnessEvent] = []
    for call in tail.poll():
        call_id = str(call.get("call_id") or call.get("id") or "").strip()
        if not call_id:
            continue
        name = str(call.get("name") or "").strip()
        arguments = _json_value(call.get("arguments"), fallback={})
        if not isinstance(arguments, Mapping):
            arguments = {"value": arguments}
        public_arguments: dict[str, Any] = dict(arguments)
        result = _json_value(call.get("output"), fallback=call.get("output"))
        child_snapshots: tuple[dict[str, Any], ...] = ()
        if name == "spawn_agent" and isinstance(result, Mapping):
            child_id = str(
                result.get("agent_id") or result.get("thread_id") or ""
            ).strip()
            if child_id:
                prompt = str(
                    public_arguments.get("message")
                    or public_arguments.get("prompt")
                    or ""
                ).strip()
                child_snapshots = (
                    _read_function_subagent(
                        client,
                        thread_id=child_id,
                        prompt=prompt or None,
                        fallback_name=str(result.get("nickname") or "").strip() or None,
                    ),
                )
                public_arguments.update(
                    {
                        "prompt": prompt or None,
                        "subagents": [
                            _public_collab_subagent(snapshot)
                            for snapshot in child_snapshots
                        ],
                    }
                )
        finished = "output" in call
        event_type = (
            HarnessEventType.TOOL_CALL_FINISHED.value
            if finished
            else HarnessEventType.TOOL_CALL_STARTED.value
        )
        payload: dict[str, Any] = {
            "tool_call_id": call_id,
            "name": name,
            "status": "completed" if finished else "inProgress",
            "arguments": public_arguments,
            "source": "codex-app-server-rollout",
        }
        if finished and result not in (None, "", (), [], {}):
            payload["result"] = result
        identity = (call_id, event_type)
        if identity not in seen:
            seen.add(identity)
            events.append(
                HarnessEvent(
                    type=event_type,
                    message=(
                        f"Codex app-server finished {name}."
                        if finished
                        else f"Codex app-server started {name}."
                    ),
                    payload=payload,
                )
            )
        if child_snapshots:
            child_id = str(child_snapshots[0].get("id") or "")
            events.extend(
                _collab_child_tool_events(
                    child_snapshots,
                    subagent_parents={child_id: call_id},
                    seen=child_seen,
                )
            )
    return tuple(events)


def _rollout_path_for_thread(
    client: AppServerClient,
    *,
    home: Path,
    thread_id: str,
) -> Path | None:
    try:
        response = client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
    except (AppServerProtocolError, OSError, RuntimeError, ValueError):
        return None
    thread = _mapping(response.get("thread"))
    return _managed_rollout_path(home, thread.get("path"))


def _tool_event_identity(event: HarnessEvent) -> tuple[str, str] | None:
    if event.type not in {
        HarnessEventType.TOOL_CALL_STARTED.value,
        HarnessEventType.TOOL_CALL_FINISHED.value,
    }:
        return None
    call_id = str(event.payload.get("tool_call_id") or "").strip()
    return (call_id, event.type) if call_id else None


def _managed_rollout_path(home: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        resolved_home = home.expanduser().resolve()
        path = Path(text).expanduser().resolve(strict=True)
    except OSError:
        return None
    if not path.is_file() or not path.is_relative_to(resolved_home):
        return None
    return path


def _read_rollout_multi_agent_calls(
    path: Path,
    *,
    turn_id: str,
) -> tuple[dict[str, Any], ...]:
    return _RolloutMultiAgentTail(path, turn_id).poll()


@dataclass
class _RolloutMultiAgentTail:
    path: Path
    turn_id: str
    offset: int = 0
    active_turn_id: str | None = None
    calls: dict[str, dict[str, Any]] = field(default_factory=dict)

    def poll(self) -> tuple[dict[str, Any], ...]:
        """Read only complete JSONL records appended since the previous poll."""
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.active_turn_id = None
                self.calls.clear()
            with self.path.open("rb") as lines:
                lines.seek(self.offset)
                content = lines.read()
        except OSError:
            return tuple(self.calls.values())
        final_newline = content.rfind(b"\n")
        if final_newline < 0:
            return tuple(self.calls.values())
        complete = content[: final_newline + 1]
        self.offset += len(complete)
        for raw_line in complete.splitlines():
            try:
                record = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, Mapping):
                continue
            record_type = str(record.get("type") or "")
            payload = _mapping(record.get("payload"))
            if record_type == "event_msg" and payload.get("type") == "task_started":
                self.active_turn_id = str(payload.get("turn_id") or "") or None
                continue
            if record_type == "turn_context":
                self.active_turn_id = (
                    str(payload.get("turn_id") or "") or self.active_turn_id
                )
                continue
            if record_type != "response_item":
                continue
            metadata = _mapping(
                payload.get("internal_chat_message_metadata_passthrough")
            )
            payload_turn_id = str(metadata.get("turn_id") or self.active_turn_id or "")
            if payload_turn_id != self.turn_id:
                continue
            payload_type = str(payload.get("type") or "")
            call_id = str(payload.get("call_id") or "").strip()
            if payload_type == "function_call":
                if str(payload.get("namespace") or "") != "multi_agent_v1":
                    continue
                if not call_id:
                    call_id = str(payload.get("id") or "").strip()
                if not call_id:
                    continue
                self.calls[call_id] = {
                    "id": payload.get("id"),
                    "call_id": call_id,
                    "name": _multi_agent_tool_name(payload.get("name")),
                    "arguments": payload.get("arguments"),
                }
            elif payload_type == "function_call_output" and call_id in self.calls:
                self.calls[call_id]["output"] = payload.get("output")
        return tuple(self.calls.values())


def _multi_agent_tool_name(value: Any) -> str:
    name = str(value or "").strip()
    for prefix in ("multi_agent_v1__", "multi_agent_v1."):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    aliases = {
        "spawnAgent": "spawn_agent",
        "sendInput": "send_input",
        "resumeAgent": "resume_agent",
        "closeAgent": "close_agent",
    }
    return aliases.get(name, name)


def _json_value(value: Any, *, fallback: Any) -> Any:
    if not isinstance(value, str):
        return value if value is not None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _read_function_subagent(
    client: AppServerClient,
    *,
    thread_id: str,
    prompt: str | None,
    fallback_name: str | None,
) -> dict[str, Any]:
    try:
        response = client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
    except (AppServerProtocolError, OSError, RuntimeError, ValueError):
        thread = {}
    else:
        thread = _mapping(response.get("thread"))
    turns = thread.get("turns") or []
    latest_turn = _mapping(turns[-1]) if turns else {}
    return {
        "id": thread_id,
        "name": (
            thread.get("agentNickname")
            or thread.get("agent_nickname")
            or fallback_name
            or thread_id[:8]
        ),
        "role": thread.get("agentRole") or thread.get("agent_role"),
        "status": latest_turn.get("status")
        or _mapping(thread.get("status")).get("type"),
        "prompt": prompt,
        "turns": turns,
    }
