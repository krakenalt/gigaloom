"""Gemini ACP protocol normalization."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence
from uuid import UUID

from gpt2giga_harness.execution import SnapshotEvidenceRef
from gpt2giga_harness.structured_processes import (
    NormalizedStructuredEvent,
    StructuredBridgeRequest,
)


from gpt2giga_harness.harnesses.builtins.gemini.acp_contracts import (
    GEMINI_ACP_PERMISSION_METHOD,
    GEMINI_ACP_PROTOCOL,
    GeminiAcpError,
    _ALLOW_KINDS,
    _REJECT_KINDS,
    _mapping,
    _validate_identity,
)


def normalize_gemini_acp_event(
    method: str, params: Mapping[str, Any]
) -> NormalizedStructuredEvent | None:
    """Normalize reviewed ACP updates without retaining raw tool input/output."""
    if method != "session/update":
        return None
    session_id = _canonical_uuid(
        params.get("sessionId"), field_name="ACP event session id"
    )
    update = _mapping(params.get("update"), field_name="ACP session update")
    kind = update.get("sessionUpdate")
    if not isinstance(kind, str) or not kind:
        raise GeminiAcpError("ACP session update kind is invalid")
    if kind in {"agent_message_chunk", "agent_thought_chunk", "user_message_chunk"}:
        content = _mapping(update.get("content"), field_name="ACP message content")
        event_type = {
            "agent_message_chunk": "output_delta",
            "agent_thought_chunk": "reasoning_delta",
            "user_message_chunk": "input_echo",
        }[kind]
        return NormalizedStructuredEvent(
            type=event_type,
            payload={"session_id": session_id, "content": dict(content)},
        )
    if kind in {"tool_call", "tool_call_update"}:
        tool_call_id = update.get("toolCallId")
        _validate_identity(tool_call_id, field_name="ACP tool call id")
        status = update.get("status")
        if status not in {None, "pending", "in_progress", "completed", "failed"}:
            raise GeminiAcpError("ACP tool status is invalid")
        event_type = {
            "pending": "tool_approval_pending",
            "in_progress": "tool_started",
            "completed": "tool_completed",
            "failed": "tool_failed",
            None: "tool_updated",
        }[status]
        payload = {
            "session_id": session_id,
            "tool_call_id": tool_call_id,
            "status": status,
        }
        for field_name in ("title", "kind", "content", "locations"):
            if field_name in update:
                payload[field_name] = update[field_name]
        return NormalizedStructuredEvent(type=event_type, payload=payload)
    if kind == "plan":
        entries = update.get("entries")
        if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
            raise GeminiAcpError("ACP plan entries are invalid")
        return NormalizedStructuredEvent(
            type="plan_update",
            payload={"session_id": session_id, "entries": list(entries)},
        )
    if kind == "usage_update":
        return NormalizedStructuredEvent(
            type="usage_update",
            payload={
                "session_id": session_id,
                "used": update.get("used"),
                "size": update.get("size"),
            },
        )
    return NormalizedStructuredEvent(
        type="session_state", payload={"session_id": session_id, "kind": kind}
    )


def _approval_contract(
    request: StructuredBridgeRequest,
    *,
    session_id: str,
    turn_id: str | None,
) -> dict[str, Any]:
    if request.method != GEMINI_ACP_PERMISSION_METHOD:
        raise GeminiAcpError("unexpected Gemini ACP permission method")
    request_session_id = _canonical_uuid(
        request.params.get("sessionId"), field_name="ACP permission session id"
    )
    if request_session_id != session_id:
        raise GeminiAcpError("Gemini ACP permission session UUID does not match")
    options = _permission_options(request.params)
    tool_call = _mapping(
        request.params.get("toolCall", {}), field_name="ACP permission toolCall"
    )
    tool_call_id = tool_call.get("toolCallId")
    _validate_identity(tool_call_id, field_name="ACP permission tool call id")
    safe_options = [
        {"option_id": option_id, "kind": kind} for option_id, kind in options
    ]
    binding = {
        "protocol": GEMINI_ACP_PROTOCOL,
        "method": request.method,
        "provider_request_id": str(request.id),
        "session_id": session_id,
        "turn_id": turn_id,
        "tool_call_id": tool_call_id,
        "options": safe_options,
    }
    return {
        **binding,
        "binding_hash": hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _permission_options(params: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    raw_options = params.get("options")
    if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes)):
        raise GeminiAcpError("ACP permission options are invalid")
    options: list[tuple[str, str]] = []
    for item in raw_options:
        option = _mapping(item, field_name="ACP permission option")
        option_id = option.get("optionId")
        kind = option.get("kind")
        _validate_identity(option_id, field_name="ACP permission option id")
        _validate_identity(kind, field_name="ACP permission option kind")
        options.append((option_id, kind))
    option_ids = [option_id for option_id, _ in options]
    if not options or len(set(option_ids)) != len(option_ids):
        raise GeminiAcpError("ACP permission options are empty or duplicated")
    return tuple(options)


def _permission_option(params: Mapping[str, Any], decision: str) -> str | None:
    options = _permission_options(params)
    exact = str(decision).strip()
    option_ids = {option_id for option_id, _ in options}
    if exact in option_ids:
        return exact
    normalized = exact.lower().replace("-", "_")
    kinds = (
        _ALLOW_KINDS
        if normalized in {"accept", "allow", "allow_once"}
        else _REJECT_KINDS
    )
    if normalized not in {
        "accept",
        "allow",
        "allow_once",
        "deny",
        "reject",
        "cancel",
    }:
        raise GeminiAcpError("Gemini ACP approval decision is invalid")
    for wanted in kinds:
        for option_id, kind in options:
            if kind == wanted:
                return option_id
    return None


def _degradation_evidence(cli_version: str) -> tuple[SnapshotEvidenceRef, ...]:
    return tuple(
        SnapshotEvidenceRef(
            id=f"gemini-acp-{capability}",
            revision=cli_version,
            status="unsupported",
            source="acp-initialize",
        )
        for capability in (
            "elicitation",
            "filesystem-client",
            "model-switch",
            "session-close",
            "session-list",
        )
    )


def _turn_status(value: Any) -> str:
    if value is None:
        return "completed"
    _validate_identity(value, field_name="ACP stop reason")
    return {
        "end_turn": "completed",
        "cancelled": "interrupted",
    }.get(value, value)


def _canonical_uuid(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise GeminiAcpError(f"{field_name} is invalid")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise GeminiAcpError(f"{field_name} is invalid") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise GeminiAcpError(f"{field_name} must be a canonical UUID")
    return canonical


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is required")
    return value


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return decoded
