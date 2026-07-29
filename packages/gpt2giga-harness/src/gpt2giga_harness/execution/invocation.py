"""Harness invocation and streaming accumulation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from typing import Any, Callable, Mapping

from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.types import (
    HarnessEvent,
    HarnessEventType,
    HarnessRequest,
    HarnessResult,
    event_to_dict,
)

MAX_REASONING_CHARACTERS = 32_768


@dataclass
class InvocationAccumulator:
    """Deduplicate streamed events while retaining usage and reasoning."""

    emitted_event_counts: Counter[str] = field(default_factory=Counter)
    latest_usage: dict[str, Any] = field(default_factory=dict)
    reasoning_parts: dict[str, list[str]] = field(
        default_factory=lambda: {"summary": [], "text": [], "model": []}
    )

    def event_sink(
        self,
        append_event: Callable[[HarnessEvent], None],
    ) -> Callable[[HarnessEvent], None]:
        """Return a sink that persists and accounts for one live event."""

        def sink(event: HarnessEvent) -> None:
            append_event(event)
            self.emitted_event_counts[_event_fingerprint(event)] += 1
            self.observe(event)

        return sink

    def was_emitted(self, event: HarnessEvent) -> bool:
        """Consume one matching live-event count for final-result deduplication."""
        fingerprint = _event_fingerprint(event)
        if self.emitted_event_counts[fingerprint] <= 0:
            return False
        self.emitted_event_counts[fingerprint] -= 1
        return True

    def observe(self, event: HarnessEvent) -> None:
        """Merge usage and reasoning from one normalized event."""
        usage = _usage_from_event(event)
        if usage is not None:
            _merge_usage(self.latest_usage, usage)
        _collect_reasoning(self.reasoning_parts, event)

    def reasoning(self) -> str:
        """Return bounded final reasoning text."""
        return _final_reasoning(self.reasoning_parts)


class HarnessExecutionService:
    """Invoke one admitted harness and normalize exceptions/cancellation."""

    def invoke(
        self,
        *,
        harness: Any,
        request: HarnessRequest,
        config_context: Any,
        durable: bool,
        structured_harness_type: type[Any],
        cancel_event: Any | None,
    ) -> HarnessResult:
        """Run the selected transport without leaking an exception past finalization."""
        try:
            if cancel_requested(cancel_event):
                result = HarnessResult(
                    ok=False,
                    text="",
                    error="Harness run canceled.",
                )
            elif (
                durable
                and request.execution_transport is ExecutionTransport.NATIVE_STRUCTURED
            ):
                if not isinstance(harness, structured_harness_type):
                    raise ValueError(
                        "durable structured admission changed after submission"
                    )
                result = harness.run_durable_structured(request, config_context)
            else:
                result = harness.run(request, config_context)
        except Exception as exc:
            result = HarnessResult(ok=False, text="", error=str(exc))
        if cancel_requested(cancel_event):
            return HarnessResult(
                ok=False,
                text="",
                raw=result.raw,
                events=result.events,
                command=result.command,
                error="Harness run canceled.",
            )
        return result


def cancel_requested(cancel_event: Any | None) -> bool:
    """Return whether the caller requested cancellation."""
    if cancel_event is None:
        return False
    checker = getattr(cancel_event, "is_set", None)
    return bool(checker()) if callable(checker) else False


def _event_fingerprint(event: HarnessEvent) -> str:
    """Return a stable fingerprint used to suppress already-streamed events."""
    return json.dumps(
        event_to_dict(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _usage_from_event(event: HarnessEvent) -> dict[str, Any] | None:
    """Extract safe token counters from one normalized usage event."""
    if event.type != HarnessEventType.USAGE.value:
        return None
    payload = event_to_dict(event)["payload"]
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_input_tokens": ("cached_input_tokens", "cached_tokens"),
        "reasoning_output_tokens": (
            "reasoning_output_tokens",
            "reasoning_tokens",
            "thoughts_tokens",
        ),
        "tool_tokens": ("tool_tokens",),
    }
    usage: dict[str, Any] = {}
    for target, keys in aliases.items():
        value = next(
            (payload[key] for key in keys if _is_nonnegative_integer(payload.get(key))),
            None,
        )
        if value is not None:
            usage[target] = value
    if (
        "total_tokens" not in usage
        and {"input_tokens", "output_tokens"} <= usage.keys()
    ):
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    source = payload.get("source")
    if isinstance(source, str) and source:
        usage["source"] = source
    return usage or None


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _collect_reasoning(
    target: dict[str, list[str]],
    event: HarnessEvent,
) -> None:
    if event.type != HarnessEventType.REASONING_DELTA.value:
        return
    payload = event_to_dict(event)["payload"]
    delta = payload.get("delta")
    if not isinstance(delta, str) or not delta:
        return
    kind = str(payload.get("kind") or "model")
    target.setdefault(kind, []).append(delta)


def _final_reasoning(parts: Mapping[str, list[str]]) -> str:
    selected = parts.get("summary") or parts.get("model") or parts.get("text") or []
    return "".join(selected)[:MAX_REASONING_CHARACTERS]


def _merge_usage(target: dict[str, Any], update: Mapping[str, Any]) -> None:
    """Merge partial usage snapshots and keep the aggregate total consistent."""
    explicit_total = "total_tokens" in update
    target.update(update)
    if (
        not explicit_total
        and _is_nonnegative_integer(target.get("input_tokens"))
        and _is_nonnegative_integer(target.get("output_tokens"))
    ):
        target["total_tokens"] = target["input_tokens"] + target["output_tokens"]
