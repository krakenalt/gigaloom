"""Bounded, provider-neutral projection of stable ACP session updates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from acp.schema import SessionNotification, UsageUpdate

from gigaloom.harnesses.acp.errors import AcpProtocolError
from gigaloom.harnesses.acp.usage import project_context_usage, usage_payload
from gigaloom.structured_processes import NormalizedStructuredEvent


_UPDATE_TYPES = {
    "user_message_chunk": "message.user.delta",
    "agent_message_chunk": "message.agent.delta",
    "agent_thought_chunk": "thought.agent.delta",
    "tool_call": "tool.started",
    "tool_call_update": "tool.updated",
    "plan": "plan.snapshot",
    "plan_update": "plan.updated",
    "plan_removed": "plan.removed",
    "available_commands_update": "commands.snapshot",
    "current_mode_update": "mode.changed",
    "config_option_update": "config.changed",
    "session_info_update": "session.info",
    "usage_update": "usage.context",
}


def normalize_acp_update(
    method: str, params: Mapping[str, Any]
) -> NormalizedStructuredEvent | None:
    """Validate stable updates and remove raw/meta extension surfaces."""
    if method != "session/update":
        return None
    try:
        notification = SessionNotification.model_validate(params)
    except Exception as exc:
        raise AcpProtocolError("ACP session update failed schema validation") from exc
    update = notification.update
    kind = str(update.session_update)
    event_type = _UPDATE_TYPES.get(kind)
    if event_type is None:
        raise AcpProtocolError("ACP session update kind is not admitted")
    if isinstance(update, UsageUpdate):
        payload = usage_payload(project_context_usage(update))
    else:
        projected = update.model_dump(mode="json", by_alias=True, exclude_none=True)
        payload = _strip_extensions(projected)
    return NormalizedStructuredEvent(
        type=event_type,
        payload={"session_id": notification.session_id, "update": payload},
    )


def _strip_extensions(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _strip_extensions(item)
            for key, item in value.items()
            if key not in {"_meta", "rawInput", "rawOutput"}
        }
    if isinstance(value, list):
        return [_strip_extensions(item) for item in value]
    return value
