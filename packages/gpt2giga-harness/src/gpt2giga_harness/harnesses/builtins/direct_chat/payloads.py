"""Direct Chat Completions harness through the local gpt2giga proxy."""

# ruff: noqa: E402, F401

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from gpt2giga_harness import proxy
from gpt2giga_harness.generated_files import (
    GeneratedFileError,
    generated_file_metadata,
    persist_generated_file,
)
from gpt2giga_harness.harnesses.attachment_plan import (
    attachment_raw_metadata,
    attachment_warning_events,
    request_render_plan,
)
from gpt2giga_harness.harnesses.base import BaseHarness
from gpt2giga_harness.gpt2giga_preset import (
    Gpt2GigaPresetUnavailableError,
    require_gpt2giga_preset,
)
from gpt2giga_harness.protocols.normalized import (
    NormalizedStreamEvent,
    NormalizedToolCall,
)
from gpt2giga_harness.types import (
    AttachmentTransportSupport,
    Availability,
    GIGACHAT_BUILTIN_TOOLS,
    HarnessChatMessage,
    HarnessCapability,
    HarnessContext,
    HarnessEvent,
    HarnessEventType,
    HeadlessContinuationStrategy,
    HarnessRequest,
    HarnessResult,
    HarnessSpec,
    emit_event,
)
from gpt2giga_harness.protocols.openai.stream_accumulator import (
    OpenAIChatCompletionStreamAccumulator,
    openai_usage_to_normalized_usage,
    stream_tool_call_to_normalized_tool_call,
)


DEFAULT_MODEL = "GigaChat"
MESSAGE_DELTA_FLUSH_CHARS = 64
MESSAGE_DELTA_FLUSH_SECONDS = 0.08


def _curl_command(
    url: str,
    payload: dict[str, object],
    include_auth: bool,
) -> tuple[str, ...]:
    command = ["curl", "-sS", url, "-H", "Content-Type: application/json"]
    if include_auth:
        command.extend(["-H", "Authorization: Bearer <redacted>"])
    command.extend(["-d", json.dumps(payload, ensure_ascii=False)])
    return tuple(command)


def _request_messages(request: HarnessRequest) -> tuple[HarnessChatMessage, ...]:
    if request.messages:
        return request.messages
    return (HarnessChatMessage(role="user", content=request.prompt),)


def _payload_messages(request: HarnessRequest) -> list[dict[str, Any]]:
    messages = [
        {"role": message.role, "content": message.content}
        for message in _request_messages(request)
    ]
    plan = request_render_plan(request)
    if not plan:
        return messages
    messages[-1]["content"] = _content_with_attachments(request, plan)
    return messages


def _content_with_attachments(
    request: HarnessRequest,
    plan: Mapping[str, Any],
) -> str | list[Mapping[str, Any]]:
    content_parts = [
        dict(part)
        for part in plan.get("content_parts", ())
        if isinstance(part, Mapping)
    ]
    prompt_prefix = str(plan.get("prompt_prefix") or "").strip()
    prompt_suffix = str(plan.get("prompt_suffix") or "").strip()
    if content_parts:
        merged_parts: list[Mapping[str, Any]] = []
        merged_text = False
        for part in content_parts:
            if part.get("type") == "text" and not merged_text:
                merged_parts.append(
                    {
                        **part,
                        "text": _join_text(
                            prompt_prefix,
                            str(part.get("text") or request.prompt),
                            prompt_suffix,
                        ),
                    }
                )
                merged_text = True
            else:
                merged_parts.append(part)
        if not merged_text and (prompt_prefix or prompt_suffix):
            merged_parts.insert(
                0,
                {
                    "type": "text",
                    "text": _join_text(prompt_prefix, request.prompt, prompt_suffix),
                },
            )
        return merged_parts
    return _join_text(prompt_prefix, request.prompt, prompt_suffix)


def _join_text(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def _emit_or_collect(
    request: HarnessRequest,
    events: list[HarnessEvent],
    event: HarnessEvent,
) -> None:
    if not emit_event(request, event):
        events.append(event)


class _TextDeltaCoalescer:
    """Batch tiny text deltas before hitting the persistent session event store."""

    def __init__(
        self,
        request: HarnessRequest,
        events: list[HarnessEvent],
        *,
        event_type: str,
        payload_key: str,
        event_message: str,
    ) -> None:
        self._request = request
        self._events = events
        self._event_type = event_type
        self._payload_key = payload_key
        self._event_message = event_message
        self._parts: list[str] = []
        self._character_count = 0
        self._started_at: float | None = None
        self._last_sequence: int | None = None

    def push(self, event: NormalizedStreamEvent) -> None:
        """Add one content delta and flush when the size/time budget is reached."""
        delta = (
            event.reasoning_delta
            if self._event_type == HarnessEventType.REASONING_DELTA.value
            else event.content_delta
        )
        if not delta:
            return
        now = time.monotonic()
        if (
            self._parts
            and self._started_at is not None
            and now - self._started_at >= MESSAGE_DELTA_FLUSH_SECONDS
        ):
            self.flush()
        if not self._parts:
            self._started_at = now
        self._parts.append(delta)
        self._character_count += len(delta)
        self._last_sequence = event.sequence
        if self._character_count >= MESSAGE_DELTA_FLUSH_CHARS:
            self.flush()

    def flush(self) -> None:
        """Publish the buffered text as one normalized message event."""
        if not self._parts:
            return
        payload: dict[str, Any] = {
            self._payload_key: "".join(self._parts),
            "source": "direct-chat",
        }
        if self._last_sequence is not None:
            payload["sequence"] = self._last_sequence
        _emit_or_collect(
            self._request,
            self._events,
            HarnessEvent(
                type=self._event_type,
                message=self._event_message,
                payload=payload,
            ),
        )
        self._parts.clear()
        self._character_count = 0
        self._started_at = None
        self._last_sequence = None

    def flush_if_due(self) -> None:
        """Publish pending text once its latency budget has elapsed."""
        if (
            self._parts
            and self._started_at is not None
            and time.monotonic() - self._started_at >= MESSAGE_DELTA_FLUSH_SECONDS
        ):
            self.flush()


def _normalized_harness_event(event: NormalizedStreamEvent) -> HarnessEvent | None:
    if event.type == "content_delta" and event.content_delta:
        return HarnessEvent(
            type=HarnessEventType.MESSAGE_DELTA.value,
            message="Assistant message delta.",
            payload={
                "delta": event.content_delta,
                "sequence": event.sequence,
                "source": "direct-chat",
            },
        )
    if event.type == "reasoning_delta" and event.reasoning_delta:
        return HarnessEvent(
            type=HarnessEventType.REASONING_DELTA.value,
            message="Assistant reasoning delta.",
            payload={
                "delta": event.reasoning_delta,
                "kind": "model",
                "sequence": event.sequence,
                "source": "direct-chat",
            },
        )
    if event.type in {"tool_call_start", "tool_call_delta"}:
        tool_call = event.tool_call
        if tool_call is None:
            return None
        payload = {
            **_tool_call_payload(tool_call),
            "source": "direct-chat",
        }
        arguments_delta = tool_call.raw_extensions.get("arguments_delta")
        if arguments_delta is not None:
            payload["arguments_delta"] = arguments_delta
        return HarnessEvent(
            type=(
                HarnessEventType.TOOL_CALL_STARTED.value
                if event.type == "tool_call_start"
                else HarnessEventType.TOOL_CALL_DELTA.value
            ),
            message=(
                f"Tool call {tool_call.name or tool_call.id or 'tool'} started."
                if event.type == "tool_call_start"
                else f"Tool call {tool_call.name or tool_call.id or 'tool'} updated."
            ),
            payload=payload,
        )
    if event.type == "usage" and event.usage is not None:
        return HarnessEvent(
            type=HarnessEventType.USAGE.value,
            message="Token usage updated.",
            payload=_usage_payload(event.usage),
        )
    if event.type == "error" and event.error is not None:
        return HarnessEvent(
            type=HarnessEventType.ERROR.value,
            message=event.error.message or "Direct chat stream failed.",
            payload={
                "type": event.error.type,
                "code": event.error.code,
                "source": "direct-chat",
            },
        )
    return None


def _usage_event(value: Any) -> HarnessEvent | None:
    usage = openai_usage_to_normalized_usage(value)
    if usage is None:
        return None
    return HarnessEvent(
        type=HarnessEventType.USAGE.value,
        message="Token usage updated.",
        payload=_usage_payload(usage),
    )


class _ProviderToolTrace:
    """Rebuild provider-internal tool nesting from response metadata."""

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, Any]] = {}
        self._call_order: list[str] = []
        self._started: set[str] = set()
        self._provider_finished: set[str] = set()
        self._active_agents: list[str] = []
        self._visible_ids: dict[str, str] = {}
        self._pending_calls: list[str] = []
        self._pending_results: list[dict[str, Any]] = []

    def register_payload(self, payload: Mapping[str, Any]) -> None:
        result_items = _provider_metadata_items(payload, "gigachat_tool_results")
        result_ids = {
            identifier
            for item in result_items
            if (identifier := _provider_tool_identifier(item)) is not None
        }
        current_ids = _payload_tool_call_ids(payload)
        for identifier in current_ids:
            self._visible_ids[_canonical_tool_id(identifier)] = identifier

        call_items = _current_provider_call_items(
            _provider_metadata_items(payload, "gigachat_called_tools"),
            current_ids=current_ids,
            result_ids=result_ids,
            known_ids=set(self._calls),
        )
        for item in call_items:
            identifier = _provider_tool_identifier(item)
            name = _provider_tool_name(item)
            if identifier is None or name is None:
                continue
            key = _canonical_tool_id(identifier)
            explicit_parent = _provider_parent_identifier(item)
            parent_key = (
                _canonical_tool_id(explicit_parent)
                if explicit_parent is not None
                else self._active_agents[-1]
                if self._active_agents and self._active_agents[-1] != key
                else None
            )
            record = self._calls.setdefault(
                key,
                {
                    "id": identifier,
                    "name": name,
                    "parent_key": parent_key,
                },
            )
            record.update(
                {
                    "id": identifier,
                    "name": name,
                    "arguments": item.get("arguments"),
                    "parent_key": parent_key or record.get("parent_key"),
                }
            )
            if key not in self._call_order:
                self._call_order.append(key)
            if key not in self._started and key not in self._pending_calls:
                self._pending_calls.append(key)
            if name == "invoke_agent" and key not in self._active_agents:
                self._active_agents.append(key)

        self._pending_results.extend(result_items)

    def enrich_event(self, event: HarnessEvent) -> HarnessEvent:
        identifier = _provider_tool_identifier(event.payload)
        if identifier is None:
            return event
        key = _canonical_tool_id(identifier)
        self._visible_ids[key] = identifier
        record = self._calls.get(key)
        payload = dict(event.payload)
        if record is not None:
            if record.get("name") is not None:
                payload.setdefault("name", record["name"])
            if record.get("arguments") is not None:
                payload.setdefault("arguments", record["arguments"])
            parent_id = self._parent_visible_id(record.get("parent_key"))
            if parent_id is not None:
                payload["parent_tool_call_id"] = parent_id
        if event.type in {
            HarnessEventType.TOOL_CALL_STARTED.value,
            HarnessEventType.TOOL_CALL_DELTA.value,
        }:
            self._started.add(key)
            if key in self._pending_calls:
                self._pending_calls.remove(key)
        return HarnessEvent(type=event.type, message=event.message, payload=payload)

    def drain_events(self) -> tuple[HarnessEvent, ...]:
        events: list[HarnessEvent] = []
        for key in self._pending_calls:
            if key in self._started:
                continue
            events.append(self._started_event(key))
            self._started.add(key)
        self._pending_calls.clear()

        for result in self._pending_results:
            key = self._result_key(result)
            if key is None or key in self._provider_finished:
                continue
            if key not in self._started:
                events.append(self._started_event(key))
                self._started.add(key)
            record = self._calls[key]
            status = str(result.get("status") or "completed")
            payload = {
                "tool_call_id": self._visible_ids.get(key, str(record["id"])),
                "name": record["name"],
                "arguments": record.get("arguments"),
                "result": result.get("result"),
                "status": status,
                "source": "direct-chat-provider",
            }
            parent_id = self._parent_visible_id(record.get("parent_key"))
            if parent_id is not None:
                payload["parent_tool_call_id"] = parent_id
            events.append(
                HarnessEvent(
                    type=HarnessEventType.TOOL_CALL_FINISHED.value,
                    message=f"Tool call {record['name']} {status}.",
                    payload={
                        key: value
                        for key, value in payload.items()
                        if value is not None
                    },
                )
            )
            self._provider_finished.add(key)
            self._close_agent(key)
        self._pending_results.clear()
        return tuple(events)

    def is_finished(self, identifier: Any) -> bool:
        return (
            isinstance(identifier, str)
            and _canonical_tool_id(identifier) in self._provider_finished
        )

    def _result_key(self, result: Mapping[str, Any]) -> str | None:
        identifier = _provider_tool_identifier(result)
        if identifier is not None:
            key = _canonical_tool_id(identifier)
            if key not in self._calls:
                name = _provider_tool_name(result) or "tool"
                parent_id = _provider_parent_identifier(result)
                parent_key = (
                    _canonical_tool_id(parent_id)
                    if parent_id is not None
                    else self._active_agents[-1]
                    if self._active_agents and name != "invoke_agent"
                    else None
                )
                self._calls[key] = {
                    "id": identifier,
                    "name": name,
                    "parent_key": parent_key,
                }
                self._call_order.append(key)
            return key
        name = _provider_tool_name(result)
        if name is None:
            return None
        return next(
            (
                key
                for key in reversed(self._call_order)
                if self._calls[key].get("name") == name
                and key not in self._provider_finished
            ),
            None,
        )

    def _started_event(self, key: str) -> HarnessEvent:
        record = self._calls[key]
        payload = {
            "tool_call_id": self._visible_ids.get(key, str(record["id"])),
            "name": record["name"],
            "arguments": record.get("arguments"),
            "status": "running",
            "source": "direct-chat-provider",
        }
        parent_id = self._parent_visible_id(record.get("parent_key"))
        if parent_id is not None:
            payload["parent_tool_call_id"] = parent_id
        return HarnessEvent(
            type=HarnessEventType.TOOL_CALL_STARTED.value,
            message=f"Tool call {record['name']} started.",
            payload={key: value for key, value in payload.items() if value is not None},
        )

    def _parent_visible_id(self, parent_key: Any) -> str | None:
        if not isinstance(parent_key, str):
            return None
        parent = self._calls.get(parent_key)
        fallback = str(parent["id"]) if parent is not None else parent_key
        return self._visible_ids.get(parent_key, fallback)

    def _close_agent(self, key: str) -> None:
        if key not in self._active_agents:
            return
        index = self._active_agents.index(key)
        del self._active_agents[index:]


from gpt2giga_harness.harnesses.builtins.direct_chat.tools import (
    _canonical_tool_id,
    _current_provider_call_items,
    _payload_tool_call_ids,
    _provider_parent_identifier,
    _provider_metadata_items,
    _provider_tool_identifier,
    _provider_tool_name,
    _tool_call_payload,
    _usage_payload,
)
