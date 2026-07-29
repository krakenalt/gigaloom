"""Direct Chat Completions harness through the local gpt2giga proxy."""

# ruff: noqa: F401

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


def _provider_metadata_items(
    payload: Mapping[str, Any],
    key: str,
) -> list[dict[str, Any]]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    raw_items = metadata.get(key)
    if not isinstance(raw_items, str) or not raw_items:
        return []
    try:
        decoded = json.loads(raw_items)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [dict(item) for item in decoded if isinstance(item, Mapping)]


def _payload_tool_call_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    identifiers: list[str] = []
    for choice in payload.get("choices") or ():
        if not isinstance(choice, Mapping):
            continue
        for message_key in ("delta", "message"):
            message = choice.get(message_key)
            if not isinstance(message, Mapping):
                continue
            for tool_call in message.get("tool_calls") or ():
                if not isinstance(tool_call, Mapping):
                    continue
                identifier = _provider_tool_identifier(tool_call)
                if identifier is not None:
                    identifiers.append(identifier)
    return tuple(identifiers)


def _current_provider_call_items(
    items: list[dict[str, Any]],
    *,
    current_ids: tuple[str, ...],
    result_ids: set[str],
    known_ids: set[str],
) -> list[dict[str, Any]]:
    current_keys = {_canonical_tool_id(identifier) for identifier in current_ids}
    result_keys = {_canonical_tool_id(identifier) for identifier in result_ids}
    candidate_indexes = [
        index
        for index, item in enumerate(items)
        if (identifier := _provider_tool_identifier(item)) is not None
        and _canonical_tool_id(identifier) in current_keys | result_keys
    ]
    if candidate_indexes:
        start = min(candidate_indexes)
        if not current_keys:
            for index in range(start - 1, -1, -1):
                if _provider_tool_name(items[index]) == "invoke_agent":
                    start = index
                    break
        return items[start:]
    return [
        item
        for item in items
        if _provider_parent_identifier(item) is not None
        or (
            (identifier := _provider_tool_identifier(item)) is not None
            and _canonical_tool_id(identifier) in known_ids
        )
    ]


def _provider_tool_identifier(value: Mapping[str, Any]) -> str | None:
    for key in ("tools_state_id", "tool_call_id", "call_id", "id"):
        identifier = value.get(key)
        if isinstance(identifier, str) and identifier.strip():
            return identifier.strip()
    return None


def _provider_parent_identifier(value: Mapping[str, Any]) -> str | None:
    parent_id = value.get("parent_tool_call_id")
    return (
        parent_id.strip() if isinstance(parent_id, str) and parent_id.strip() else None
    )


def _provider_tool_name(value: Mapping[str, Any]) -> str | None:
    name = value.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def _canonical_tool_id(identifier: str) -> str:
    for prefix in ("fc_", "call_"):
        if identifier.startswith(prefix) and len(identifier) > len(prefix):
            return identifier.removeprefix(prefix)
    return identifier


def _builtin_tool_execution_events(
    payload: Mapping[str, Any],
    *,
    seen: set[str] | None = None,
    final: bool = False,
) -> tuple[HarnessEvent, ...]:
    """Normalize GigaChat built-in tool metadata into Harness tool events."""
    seen = seen if seen is not None else set()
    events = []
    messages: list[Mapping[str, Any]] = []
    for choice in payload.get("choices") or ():
        if isinstance(choice, Mapping):
            messages.extend(
                message
                for message_key in ("delta", "message")
                if isinstance((message := choice.get(message_key)), Mapping)
            )
    messages.extend(
        message
        for message in payload.get("messages") or ()
        if isinstance(message, Mapping)
    )
    for message in messages:
        executions = list(message.get("tool_executions") or ())
        for part in message.get("content") or ():
            if isinstance(part, Mapping) and isinstance(
                part.get("tool_execution"), Mapping
            ):
                executions.append(part["tool_execution"])
        for execution in executions:
            if not isinstance(execution, Mapping):
                continue
            name = str(execution.get("name") or "").strip()
            if not name:
                continue
            tool_call_id = f"builtin:{name}"
            status = _builtin_tool_status(execution.get("status"), final=final)
            if status in {"completed", "failed"}:
                event_type = HarnessEventType.TOOL_CALL_FINISHED.value
            elif tool_call_id in seen:
                event_type = HarnessEventType.TOOL_CALL_DELTA.value
            else:
                event_type = HarnessEventType.TOOL_CALL_STARTED.value
            seen.add(tool_call_id)
            event_payload = {
                "tool_call_id": tool_call_id,
                "name": name,
                "type": "builtin",
                "status": status,
                "source": "direct-chat",
            }
            if execution.get("seconds_left") is not None:
                event_payload["seconds_left"] = execution["seconds_left"]
            events.append(
                HarnessEvent(
                    type=event_type,
                    message=f"Built-in tool {name} {status}.",
                    payload=event_payload,
                )
            )
    return tuple(events)


def _builtin_tool_status(value: Any, *, final: bool) -> str:
    status = str(value or "").lower()
    if status in {"success", "completed"}:
        return "completed"
    if status in {"failed", "error"}:
        return "failed"
    return "completed" if final else "running"


def _generated_file_events(
    payload: Mapping[str, Any],
    *,
    request: HarnessRequest,
    context: HarnessContext,
    seen: set[str] | None = None,
) -> tuple[HarnessEvent, ...]:
    """Fetch GigaChat-generated files and expose safe Harness file events."""
    seen = seen if seen is not None else set()
    events = []
    for file_data in _generated_file_items(payload):
        metadata = generated_file_metadata(file_data)
        if metadata is None:
            continue
        file_id, mime_type, target = metadata
        if file_id in seen:
            continue
        seen.add(file_id)
        try:
            generated = _fetch_generated_file(
                file_data,
                request=request,
                context=context,
                file_id=file_id,
                mime_type=mime_type,
                target=target,
            )
        except Exception as exc:
            events.append(
                HarnessEvent(
                    type=HarnessEventType.WARNING.value,
                    message="Generated file could not be fetched.",
                    payload={
                        "file_id": file_id,
                        "error_type": type(exc).__name__,
                        "source": "direct-chat",
                    },
                )
            )
            continue
        events.append(
            HarnessEvent(
                type=HarnessEventType.GENERATED_FILE.value,
                message="Generated file is ready.",
                payload={**generated, "source": "direct-chat"},
            )
        )
    return tuple(events)


def _generated_file_items(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    messages: list[Mapping[str, Any]] = []
    for choice in payload.get("choices") or ():
        if not isinstance(choice, Mapping):
            continue
        messages.extend(
            message
            for message_key in ("delta", "message")
            if isinstance((message := choice.get(message_key)), Mapping)
        )
    messages.extend(
        message
        for message in payload.get("messages") or ()
        if isinstance(message, Mapping)
    )
    files: list[Mapping[str, Any]] = []
    for message in messages:
        files.extend(
            item for item in message.get("files") or () if isinstance(item, Mapping)
        )
        for content_part in message.get("content") or ():
            if not isinstance(content_part, Mapping):
                continue
            files.extend(
                item
                for item in content_part.get("files") or ()
                if isinstance(item, Mapping)
            )
    return tuple(files)


def _fetch_generated_file(
    file_data: Mapping[str, Any],
    *,
    request: HarnessRequest,
    context: HarnessContext,
    file_id: str,
    mime_type: str,
    target: str,
) -> dict[str, Any]:
    if not context.data_dir or not request.run_id:
        raise GeneratedFileError("generated files require a managed Harness run")
    content = file_data.get("content")
    if not isinstance(content, str) or not content:
        from gpt2giga_harness.harnesses.builtins.direct_chat import adapter

        content = (
            adapter._download_gigachat_image(file_id)
            if target == "image"
            else adapter._download_gigachat_file(file_id)
        )
    return persist_generated_file(
        context.data_dir,
        run_id=request.run_id,
        file_id=file_id,
        mime_type=mime_type,
        target=target,
        content_base64=content,
    )


def _download_gigachat_image(file_id: str) -> str:
    """Download a generated image with the same config as the local proxy."""
    return _download_gigachat_file(file_id)


def _download_gigachat_file(file_id: str) -> str:
    """Download generated bytes through the SDK file-content endpoint."""
    from gpt2giga_harness.harnesses.builtins.direct_chat import adapter

    try:
        runtime = adapter.require_gpt2giga_preset()
    except Gpt2GigaPresetUnavailableError as exc:
        raise GeneratedFileError(str(exc)) from exc
    settings = runtime.load_config().gigachat_settings
    client = runtime.client_type(**settings.model_dump())
    try:
        # gigachat 0.2 exposes /files/{id}/content as get_image for every MIME type.
        file_response = client.get_image(file_id)
        content = getattr(file_response, "content", None)
        if not isinstance(content, str) or not content:
            raise GeneratedFileError("GigaChat returned empty generated file content")
        return content
    finally:
        client.close()


def _usage_payload(usage: Any) -> dict[str, Any]:
    payload = {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "source": "direct-chat",
    }
    raw = dict(usage.raw_extensions)
    prompt_details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    if isinstance(prompt_details, Mapping):
        payload["cached_input_tokens"] = prompt_details.get("cached_tokens")
    if isinstance(completion_details, Mapping):
        payload["reasoning_output_tokens"] = completion_details.get("reasoning_tokens")
    for key in ("cached_input_tokens", "reasoning_output_tokens"):
        if raw.get(key) is not None:
            payload[key] = raw[key]
    return {key: item for key, item in payload.items() if item is not None}


def _tool_call_payload(tool_call: NormalizedToolCall) -> dict[str, Any]:
    index = tool_call.raw_extensions.get("index")
    tool_call_id = tool_call.id or (f"tool_{index}" if index is not None else None)
    return {
        key: value
        for key, value in {
            "tool_call_id": tool_call_id,
            "name": tool_call.name,
            "type": tool_call.type,
            "arguments": tool_call.arguments,
        }.items()
        if value is not None
    }
