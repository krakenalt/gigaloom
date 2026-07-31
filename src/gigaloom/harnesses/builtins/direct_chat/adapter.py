"""Direct Chat Completions harness through the local gpt2giga proxy."""

# ruff: noqa: E402, F401

from __future__ import annotations

import json
import time
from typing import Any, Mapping

from gigaloom import proxy
from gigaloom.generated_files import (
    GeneratedFileError,
    generated_file_metadata,
    persist_generated_file,
)
from gigaloom.harnesses.attachment_plan import (
    attachment_raw_metadata,
    attachment_warning_events,
    request_render_plan,
)
from gigaloom.harnesses.base import BaseHarness
from gigaloom.gpt2giga_preset import (
    Gpt2GigaPresetUnavailableError,
    require_gpt2giga_preset,
)
from gigaloom.protocols.normalized import (
    NormalizedStreamEvent,
    NormalizedToolCall,
)
from gigaloom.types import (
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
from gigaloom.protocols.openai.stream_accumulator import (
    OpenAIChatCompletionStreamAccumulator,
    openai_usage_to_normalized_usage,
    stream_tool_call_to_normalized_tool_call,
)


DEFAULT_MODEL = "GigaChat"
MESSAGE_DELTA_FLUSH_CHARS = 64
MESSAGE_DELTA_FLUSH_SECONDS = 0.08


class DirectChatHarness(BaseHarness):
    """Call /v1 or /v2 Chat Completions on the local proxy."""

    @classmethod
    def spec(cls) -> HarnessSpec:
        return HarnessSpec(
            id="direct-chat",
            title="Direct Chat Completions",
            kind="built-in",
            description=("Direct OpenAI-style Chat Completions through gpt2giga"),
            capabilities=(HarnessCapability.CHAT_COMPLETIONS,),
            supports_model_selection=True,
            supports_api_mode_selection=True,
            supports_streaming=True,
            supports_structured_events=True,
            supports_cancellation=True,
            supports_attachments=True,
            accepted_attachment_kinds=(
                "image",
                "text",
                "workspace_file",
                "document",
            ),
            attachment_transport=(
                "openai_content_parts",
                "inline_text",
                "gigachat_file_upload",
            ),
            attachment_capabilities={
                "image": AttachmentTransportSupport(
                    headless=("openai_content_parts",),
                    rich=True,
                    detail="Stored images are sent as OpenAI-style image content parts.",
                ),
                "text": AttachmentTransportSupport(
                    headless=("inline_text",),
                    detail="Small text attachments are inlined with a size limit.",
                ),
                "workspace_file": AttachmentTransportSupport(
                    headless=("prompt_path_reference",),
                    detail="Workspace files remain contained path references.",
                ),
                "document": AttachmentTransportSupport(
                    headless=("gigachat_file_upload",),
                    rich=True,
                    detail=(
                        "Stored documents are uploaded to GigaChat Files through "
                        "gpt2giga and attached to the user message."
                    ),
                ),
            },
            supported_builtin_tools=GIGACHAT_BUILTIN_TOOLS,
            headless_continuation=HeadlessContinuationStrategy.STRUCTURED_REPLAY,
            tags=("chat", "proxy"),
        )

    def availability(self) -> Availability:
        return Availability.available("built-in harness")

    def run(
        self,
        request: HarnessRequest,
        context: HarnessContext,
    ) -> HarnessResult:
        model = request.model or context.default_model or DEFAULT_MODEL
        url = proxy.build_chat_completions_url(context.proxy_url, request.api_mode)
        payload = {
            "model": model,
            "messages": _payload_messages(request),
            "stream": bool(request.stream),
        }
        adapter_options = request.extra.get("agent_adapter_options")
        reasoning_effort = (
            adapter_options.get("reasoning_effort")
            if isinstance(adapter_options, Mapping)
            else None
        )
        if reasoning_effort in {"low", "medium", "high"}:
            payload["reasoning_effort"] = reasoning_effort
        if request.builtin_tools:
            payload["tools"] = [{"type": tool.value} for tool in request.builtin_tools]
        api_key = context.api_key or proxy.cached_sidecar_api_key(context.proxy_url)
        cli_command = (
            "giga",
            "chat",
            "--api-mode",
            request.api_mode.value,
            "--model",
            model,
            request.prompt,
        )
        curl_command = _curl_command(url, payload, bool(api_key))
        if request.extra.get("dry_run"):
            return HarnessResult(
                ok=True,
                text="dry run",
                raw={
                    "url": url,
                    "payload": payload,
                    "curl_command": curl_command,
                    **attachment_raw_metadata(request),
                },
                events=attachment_warning_events(request),
                command=cli_command,
            )
        events = attachment_warning_events(request)
        if context.auto_start_proxy:
            startup = proxy.ensure_proxy_available(context, request.api_mode)
            api_key = startup.api_key or api_key
            curl_command = _curl_command(url, payload, bool(api_key))
            if not startup.ok:
                return HarnessResult(
                    ok=False,
                    text="",
                    raw={
                        "url": url,
                        "payload": payload,
                        "curl_command": curl_command,
                        **attachment_raw_metadata(request),
                        "proxy_start": {
                            "started": startup.started,
                            "detail": startup.detail,
                            "error": startup.error,
                        },
                    },
                    command=cli_command,
                    error=startup.error or "proxy is not reachable",
                )
            if startup.started:
                events = (
                    *events,
                    HarnessEvent(
                        type="proxy_sidecar",
                        message="Started local gpt2giga proxy sidecar.",
                        payload={
                            "proxy_url": context.proxy_url,
                            "pid": startup.pid,
                        },
                    ),
                )
        try:
            if request.stream:
                return self._run_stream(
                    request=request,
                    context=context,
                    url=url,
                    payload=payload,
                    api_key=api_key,
                    cli_command=cli_command,
                    curl_command=curl_command,
                    events=events,
                )
            data = proxy.request_json(
                "POST",
                url,
                payload=payload,
                api_key=api_key,
                timeout=context.timeout_seconds,
            )
        except proxy.ProxyRequestError as exc:
            return HarnessResult(
                ok=False,
                text="",
                raw={
                    "url": url,
                    "payload": payload,
                    "curl_command": curl_command,
                    **attachment_raw_metadata(request),
                },
                command=cli_command,
                events=events,
                error=str(exc),
            )
        events = list(events)
        provider_trace = _ProviderToolTrace()
        provider_trace.register_payload(data)
        for event in provider_trace.drain_events():
            _emit_or_collect(request, events, event)
        for event in _builtin_tool_execution_events(data, final=True):
            _emit_or_collect(request, events, event)
        for event in _generated_file_events(data, request=request, context=context):
            _emit_or_collect(request, events, event)
        usage_event = _usage_event(data.get("usage"))
        if usage_event is not None:
            _emit_or_collect(request, events, usage_event)
        return HarnessResult(
            ok=True,
            text=proxy.extract_text(data),
            raw={
                **proxy.safe_raw(data),
                "url": url,
                "curl_command": curl_command,
                **attachment_raw_metadata(request),
            },
            events=tuple(events),
            command=cli_command,
        )

    def _run_stream(
        self,
        *,
        request: HarnessRequest,
        context: HarnessContext,
        url: str,
        payload: dict[str, Any],
        api_key: str | None,
        cli_command: tuple[str, ...],
        curl_command: tuple[str, ...],
        events: tuple[HarnessEvent, ...],
    ) -> HarnessResult:
        pending_events: list[HarnessEvent] = []
        for event in events:
            _emit_or_collect(request, pending_events, event)
        accumulator = OpenAIChatCompletionStreamAccumulator()
        provider_trace = _ProviderToolTrace()
        builtin_tools_seen: set[str] = set()
        generated_files_seen: set[str] = set()
        message_deltas = _TextDeltaCoalescer(
            request,
            pending_events,
            event_type=HarnessEventType.MESSAGE_DELTA.value,
            payload_key="delta",
            event_message="Assistant message delta.",
        )
        reasoning_deltas = _TextDeltaCoalescer(
            request,
            pending_events,
            event_type=HarnessEventType.REASONING_DELTA.value,
            payload_key="delta",
            event_message="Assistant reasoning delta.",
        )

        def flush_due_deltas() -> None:
            message_deltas.flush_if_due()
            reasoning_deltas.flush_if_due()

        chunk_count = 0
        last_payload: Mapping[str, Any] = {}
        try:
            for stream_payload in proxy.stream_sse_json(
                "POST",
                url,
                payload=payload,
                api_key=api_key,
                timeout=context.timeout_seconds,
                cancel_event=request.cancel_event,
                idle_callback=flush_due_deltas,
            ):
                chunk_count += 1
                last_payload = stream_payload
                provider_trace.register_payload(stream_payload)
                for event in _builtin_tool_execution_events(
                    stream_payload,
                    seen=builtin_tools_seen,
                ):
                    message_deltas.flush()
                    reasoning_deltas.flush()
                    _emit_or_collect(request, pending_events, event)
                for event in _generated_file_events(
                    stream_payload,
                    request=request,
                    context=context,
                    seen=generated_files_seen,
                ):
                    message_deltas.flush()
                    reasoning_deltas.flush()
                    _emit_or_collect(request, pending_events, event)
                for normalized_event in accumulator.observe_payload(stream_payload):
                    if normalized_event.type == "content_delta":
                        reasoning_deltas.flush()
                        message_deltas.push(normalized_event)
                        continue
                    if normalized_event.type == "reasoning_delta":
                        message_deltas.flush()
                        reasoning_deltas.push(normalized_event)
                        continue
                    message_deltas.flush()
                    reasoning_deltas.flush()
                    event = _normalized_harness_event(normalized_event)
                    if event is not None:
                        event = provider_trace.enrich_event(event)
                        _emit_or_collect(request, pending_events, event)
                for event in provider_trace.drain_events():
                    message_deltas.flush()
                    reasoning_deltas.flush()
                    _emit_or_collect(request, pending_events, event)
        except proxy.ProxyRequestError as exc:
            message_deltas.flush()
            reasoning_deltas.flush()
            return HarnessResult(
                ok=False,
                text="",
                raw={
                    "url": url,
                    "payload": payload,
                    "curl_command": curl_command,
                    "stream": True,
                    "chunk_count": chunk_count,
                    **attachment_raw_metadata(request),
                },
                command=cli_command,
                events=tuple(pending_events),
                error=str(exc),
            )

        message_deltas.flush()
        reasoning_deltas.flush()
        response = accumulator.to_normalized_response()
        for index, raw_tool_call in sorted(accumulator.tool_calls.items()):
            tool_call = stream_tool_call_to_normalized_tool_call(raw_tool_call)
            tool_call.raw_extensions["index"] = index
            if provider_trace.is_finished(tool_call.id):
                continue
            event = provider_trace.enrich_event(
                HarnessEvent(
                    type=HarnessEventType.TOOL_CALL_FINISHED.value,
                    message=f"Tool call {tool_call.name or tool_call.id or index} assembled.",
                    payload={
                        **_tool_call_payload(tool_call),
                        "status": "requested",
                        "source": "direct-chat",
                    },
                )
            )
            _emit_or_collect(
                request,
                pending_events,
                event,
            )
        error = response.error.message if response.error is not None else None
        return HarnessResult(
            ok=error is None,
            text="".join(accumulator.content_parts),
            raw={
                "url": url,
                "curl_command": curl_command,
                "stream": True,
                "chunk_count": chunk_count,
                "last_payload": proxy.safe_raw(dict(last_payload)),
                "response": response.to_json_dict(),
                **attachment_raw_metadata(request),
            },
            events=tuple(pending_events),
            command=cli_command,
            error=error,
        )


from gigaloom.harnesses.builtins.direct_chat.payloads import (
    _ProviderToolTrace,
    _TextDeltaCoalescer,
    _curl_command,
    _emit_or_collect,
    _normalized_harness_event,
    _payload_messages,
    _usage_event,
)
from gigaloom.harnesses.builtins.direct_chat.tools import (
    _builtin_tool_execution_events,
    _download_gigachat_file,
    _download_gigachat_image,
    _generated_file_events,
    _tool_call_payload,
)

DirectChatHarness.__module__ = "gigaloom.harnesses.direct_chat"
