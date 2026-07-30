"""Terminal result projection for one Harness invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from gigaloom.types import HarnessEventType, HarnessResult


@dataclass(frozen=True)
class TerminalRunOutcome:
    """Normalized terminal state persisted by the runner facade."""

    status: str
    role: str
    content: str
    event_type: str
    event_message: str
    error: str | None
    message_metadata: Mapping[str, Any]


class RunFinalizationService:
    """Project invocation state into one deterministic terminal outcome."""

    def resolve(
        self,
        result: HarnessResult,
        *,
        canceled: bool,
        latest_usage: Mapping[str, Any],
        reasoning: str,
    ) -> TerminalRunOutcome:
        """Resolve status, message, event, and error fields exactly once."""
        if canceled:
            status = "canceled"
            role = "error"
            content = "Harness run canceled."
            event_type = HarnessEventType.RUN_CANCELED.value
            event_message = "Harness run canceled."
            error = content
        elif result.ok:
            status = "succeeded"
            role = "assistant"
            content = result.text
            event_type = HarnessEventType.MESSAGE_COMPLETED.value
            event_message = "Assistant message completed."
            error = None
        else:
            status = "failed"
            role = "error"
            content = result.error or result.text or "Harness run failed"
            event_type = HarnessEventType.ERROR.value
            event_message = "Harness run failed."
            error = content
        message_metadata: dict[str, Any] = {}
        if role == "assistant" and latest_usage:
            message_metadata["usage"] = dict(latest_usage)
        if role == "assistant" and reasoning:
            message_metadata["reasoning"] = reasoning
        return TerminalRunOutcome(
            status=status,
            role=role,
            content=content,
            event_type=event_type,
            event_message=event_message,
            error=error,
            message_metadata=message_metadata,
        )
