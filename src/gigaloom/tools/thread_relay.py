"""Restricted agent tool surface for bounded Thread Relay actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.tools.base import ToolDescriptor, ToolRisk


THREAD_RELAY_TOOL_PROVIDER_ID = "gigaloom-thread-relay"
THREAD_RELAY_APPROVAL_OWNER = "thread_relay.agent_send"
THREAD_RELAY_TOOL_IDS = (
    "thread.list",
    "thread.read",
    "thread.send",
    "thread.status",
)
_COMMON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
_SOURCES = ["gigaloom", "codex", "acp"]
_INTENTS = ["message", "follow_up", "steer"]


class ThreadRelayToolActions(Protocol):
    """Structurally bind the restricted surface to one action owner."""

    def list_threads(
        self,
        *,
        source: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]: ...

    def read_thread(
        self,
        *,
        source: str,
        thread_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]: ...

    def preview_agent_send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def send_agent_approved(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
        approval_receipt_ref: str,
    ) -> Mapping[str, Any]: ...

    def status(self, delivery_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ThreadRelayToolScope:
    """Authority scope fixed before tool discovery or invocation."""

    actor_scope: str
    project_id: str

    def __post_init__(self) -> None:
        if not self.actor_scope.strip() or not self.project_id.strip():
            raise ValueError("thread tool actor and project scope are required")


def thread_relay_approval_binding(
    scope: ThreadRelayToolScope,
    preview_digest: str,
) -> str:
    """Bind one approval grant to the exact actor, project, and safe preview."""
    if len(preview_digest) != 64 or any(
        character not in "0123456789abcdef" for character in preview_digest
    ):
        raise ValueError("thread tool preview digest is invalid")
    return canonical_digest(
        {
            "actor_scope": scope.actor_scope,
            "preview_digest": preview_digest,
            "project_id": scope.project_id,
        }
    )


class RestrictedThreadRelayTools:
    """Expose exactly four bounded tools without raw history or admin actions."""

    id = THREAD_RELAY_TOOL_PROVIDER_ID

    def __init__(
        self,
        *,
        scope: ThreadRelayToolScope,
        actions: ThreadRelayToolActions,
    ) -> None:
        self.scope = scope
        self.actions = actions

    def list_tools(self) -> tuple[ToolDescriptor, ...]:
        """Return immutable descriptors for the admitted surface only."""
        return _DESCRIPTORS

    def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        """Invoke one actor/project-bound action with strict argument bounds."""
        if tool_id == "thread.list":
            _reject_unknown(arguments, {"source", "cursor", "limit"})
            return self.actions.list_threads(
                source=_choice(arguments.get("source", "gigaloom"), _SOURCES, "source"),
                cursor=_optional_text(arguments.get("cursor"), "cursor", maximum=1024),
                limit=_limit(arguments.get("limit", 50)),
            )
        if tool_id == "thread.read":
            _reject_unknown(arguments, {"source", "thread_id", "cursor", "limit"})
            return self.actions.read_thread(
                source=_choice(arguments.get("source", "gigaloom"), _SOURCES, "source"),
                thread_id=_text(arguments.get("thread_id"), "thread_id", maximum=256),
                cursor=_optional_text(arguments.get("cursor"), "cursor", maximum=1024),
                limit=_limit(arguments.get("limit", 50)),
            )
        if tool_id == "thread.status":
            _reject_unknown(arguments, {"delivery_id"})
            return self.actions.status(
                _text(arguments.get("delivery_id"), "delivery_id", maximum=256)
            )
        if tool_id == "thread.send":
            return self._send(arguments)
        raise ValueError("thread tool is not admitted")

    def _send(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = {
            "approval_receipt_ref",
            "attachment_refs",
            "expected_active_turn_id",
            "expected_target_revision",
            "expires_at",
            "idempotency_key",
            "intent",
            "preview_digest",
            "source",
            "source_thread_id",
            "text",
            "thread_id",
        }
        _reject_unknown(arguments, allowed)
        payload = {
            "source": _choice(
                arguments.get("source", "gigaloom"),
                _SOURCES,
                "source",
            ),
            "source_thread_id": _optional_text(
                arguments.get("source_thread_id"),
                "source_thread_id",
                maximum=256,
            ),
            "thread_id": _text(
                arguments.get("thread_id"),
                "thread_id",
                maximum=256,
            ),
            "text": _text(arguments.get("text"), "text", maximum=16_384),
            "intent": _choice(
                arguments.get("intent", "message"),
                _INTENTS,
                "intent",
            ),
            "author_mode": "agent_proposed_user_approved",
            "expected_target_revision": _text(
                arguments.get("expected_target_revision"),
                "expected_target_revision",
                maximum=256,
            ),
            "expected_active_turn_id": _optional_text(
                arguments.get("expected_active_turn_id"),
                "expected_active_turn_id",
                maximum=256,
            ),
            "idempotency_key": _text(
                arguments.get("idempotency_key"),
                "idempotency_key",
                maximum=256,
            ),
            "expires_at": _text(
                arguments.get("expires_at"),
                "expires_at",
                maximum=128,
            ),
            "attachment_refs": list(_text_list(arguments.get("attachment_refs", ()))),
        }
        preview = _validated_preview(self.actions.preview_agent_send(payload))
        expected_preview = _optional_text(
            arguments.get("preview_digest"),
            "preview_digest",
            maximum=64,
        )
        approval_ref = _optional_text(
            arguments.get("approval_receipt_ref"),
            "approval_receipt_ref",
            maximum=256,
        )
        if expected_preview is None or approval_ref is None:
            return {
                "requires_user_approval": True,
                "preview": preview,
            }
        if expected_preview != preview["preview_digest"]:
            raise PermissionError("thread tool preview changed; request approval again")
        return {
            "requires_user_approval": False,
            "approval_receipt_ref": approval_ref,
            "preview": preview,
            "delivery": dict(
                self.actions.send_agent_approved(
                    payload,
                    preview_digest=expected_preview,
                    approval_receipt_ref=approval_ref,
                )
            ),
        }


def _object_schema(
    properties: Mapping[str, Any],
    *,
    required: tuple[str, ...] = (),
) -> Mapping[str, Any]:
    return {
        "$schema": _COMMON_SCHEMA,
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


_SOURCE_SCHEMA = {"type": "string", "enum": _SOURCES, "default": "gigaloom"}
_CURSOR_SCHEMA = {"type": ["string", "null"], "maxLength": 1024}
_LIMIT_SCHEMA = {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}
_DESCRIPTORS = (
    ToolDescriptor(
        id="thread.list",
        provider_id=THREAD_RELAY_TOOL_PROVIDER_ID,
        title="List permitted threads",
        description="List one bounded page in the already bound actor and project.",
        input_schema=_object_schema(
            {"source": _SOURCE_SCHEMA, "cursor": _CURSOR_SCHEMA, "limit": _LIMIT_SCHEMA}
        ),
        risk=ToolRisk.LOW,
        policy_id="thread-relay-bounded-read",
        tags=("thread-relay", "read"),
    ),
    ToolDescriptor(
        id="thread.read",
        provider_id=THREAD_RELAY_TOOL_PROVIDER_ID,
        title="Read a permitted thread",
        description="Read one bounded visible projection without raw history.",
        input_schema=_object_schema(
            {
                "source": _SOURCE_SCHEMA,
                "thread_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "cursor": _CURSOR_SCHEMA,
                "limit": _LIMIT_SCHEMA,
            },
            required=("thread_id",),
        ),
        risk=ToolRisk.LOW,
        policy_id="thread-relay-bounded-read",
        tags=("thread-relay", "read"),
    ),
    ToolDescriptor(
        id="thread.send",
        provider_id=THREAD_RELAY_TOOL_PROVIDER_ID,
        title="Propose a thread delivery",
        description="Preview first; delivery requires an exact user approval receipt.",
        input_schema=_object_schema(
            {
                "source": _SOURCE_SCHEMA,
                "source_thread_id": {"type": ["string", "null"], "maxLength": 256},
                "thread_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "text": {"type": "string", "minLength": 1, "maxLength": 16_384},
                "intent": {"type": "string", "enum": _INTENTS, "default": "message"},
                "expected_target_revision": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 256,
                },
                "expected_active_turn_id": {
                    "type": ["string", "null"],
                    "maxLength": 256,
                },
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 256},
                "expires_at": {
                    "type": "string",
                    "format": "date-time",
                    "maxLength": 128,
                },
                "attachment_refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 256},
                    "maxItems": 16,
                    "default": [],
                },
                "preview_digest": {
                    "type": ["string", "null"],
                    "pattern": "^[0-9a-f]{64}$",
                },
                "approval_receipt_ref": {"type": ["string", "null"], "maxLength": 256},
            },
            required=(
                "thread_id",
                "text",
                "expected_target_revision",
                "idempotency_key",
                "expires_at",
            ),
        ),
        risk=ToolRisk.HIGH,
        policy_id="thread-relay-user-approval",
        tags=("thread-relay", "mutation", "user-approval"),
    ),
    ToolDescriptor(
        id="thread.status",
        provider_id=THREAD_RELAY_TOOL_PROVIDER_ID,
        title="Read a thread delivery receipt",
        description="Read the current digest-only receipt, including cancellation.",
        input_schema=_object_schema(
            {"delivery_id": {"type": "string", "minLength": 1, "maxLength": 256}},
            required=("delivery_id",),
        ),
        risk=ToolRisk.LOW,
        policy_id="thread-relay-bounded-read",
        tags=("thread-relay", "read", "receipt"),
    ),
)


def _validated_preview(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    digest = payload.get("preview_digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("thread tool preview digest is invalid")
    forbidden = {"content", "message", "prompt", "text"}
    if _mapping_keys(payload) & forbidden:
        raise ValueError("thread tool preview must not echo message content")
    return payload


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        keys = {str(key).lower() for key in value}
        for item in value.values():
            keys.update(_mapping_keys(item))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys.update(_mapping_keys(item))
        return keys
    return set()


def _reject_unknown(value: Mapping[str, Any], allowed: set[str]) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ValueError(f"thread tool arguments are not admitted: {unexpected[0]}")


def _choice(value: object, choices: list[str], field_name: str) -> str:
    parsed = _text(value, field_name, maximum=64)
    if parsed not in choices:
        raise ValueError(f"thread tool {field_name} is invalid")
    return parsed


def _text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"thread tool {field_name} is invalid")
    return value.strip()


def _optional_text(value: object, field_name: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field_name, maximum=maximum)


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        raise ValueError("thread tool attachment_refs are invalid")
    return tuple(_text(item, "attachment_ref", maximum=256) for item in value)


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("thread tool limit is invalid")
    return value


__all__ = [
    "THREAD_RELAY_APPROVAL_OWNER",
    "THREAD_RELAY_TOOL_IDS",
    "THREAD_RELAY_TOOL_PROVIDER_ID",
    "RestrictedThreadRelayTools",
    "ThreadRelayToolActions",
    "ThreadRelayToolScope",
    "thread_relay_approval_binding",
]
