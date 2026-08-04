"""Strict JSON codecs for bounded Thread Relay contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.operational_validation import parse_timestamp, require_mapping
from gigaloom.sessions.thread_relay.contracts import (
    ThreadActiveTurnV1,
    ThreadAuthorMode,
    ThreadDeliveryIntent,
    ThreadDeliveryReceiptV1,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    ThreadReadProjectionV1,
    ThreadRelationshipKind,
    ThreadRelationshipV1,
    ThreadSourceKind,
    ThreadVisibleMessageV1,
    ThreadVisibleRole,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def thread_locator_to_dict(value: ThreadLocatorV1) -> dict[str, Any]:
    """Serialize one scoped thread locator."""
    return {
        "schema_version": value.schema_version,
        "source_kind": value.source_kind.value,
        "adapter_id": value.adapter_id,
        "project_id": value.project_id,
        "thread_id": value.thread_id,
        "actor_scope": value.actor_scope,
        "workspace_identity": value.workspace_identity,
        "provider_session_ref": value.provider_session_ref,
        "capability_revision": value.capability_revision,
    }


def thread_locator_from_dict(payload: Mapping[str, Any]) -> ThreadLocatorV1:
    """Decode one strict scoped thread locator."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "source_kind",
            "adapter_id",
            "project_id",
            "thread_id",
            "actor_scope",
            "workspace_identity",
            "provider_session_ref",
            "capability_revision",
        },
        field_name="thread locator",
    )
    return ThreadLocatorV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        source_kind=_enum(ThreadSourceKind, value["source_kind"], "source_kind"),
        adapter_id=_string(value["adapter_id"], "adapter_id"),
        project_id=_string(value["project_id"], "project_id"),
        thread_id=_string(value["thread_id"], "thread_id"),
        actor_scope=_string(value["actor_scope"], "actor_scope"),
        workspace_identity=_optional_string(
            value["workspace_identity"], "workspace_identity"
        ),
        provider_session_ref=_optional_string(
            value["provider_session_ref"], "provider_session_ref"
        ),
        capability_revision=_string(
            value["capability_revision"], "capability_revision"
        ),
    )


def thread_read_projection_to_dict(value: ThreadReadProjectionV1) -> dict[str, Any]:
    """Serialize one bounded thread read projection."""
    return {
        "schema_version": value.schema_version,
        "locator": thread_locator_to_dict(value.locator),
        "title": value.title,
        "status": value.status,
        "updated_at": value.updated_at.isoformat(),
        "visible_messages": [_message_to_dict(item) for item in value.visible_messages],
        "active_turn": (
            _active_turn_to_dict(value.active_turn)
            if value.active_turn is not None
            else None
        ),
        "route": value.route,
        "model": value.model,
        "relationships": [_relationship_to_dict(item) for item in value.relationships],
        "next_cursor": value.next_cursor,
        "omitted_count": value.omitted_count,
        "redaction_facts": list(value.redaction_facts),
        "unsupported_facts": list(value.unsupported_facts),
    }


def thread_read_projection_from_dict(
    payload: Mapping[str, Any],
) -> ThreadReadProjectionV1:
    """Decode one strict bounded thread read projection."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "locator",
            "title",
            "status",
            "updated_at",
            "visible_messages",
            "active_turn",
            "route",
            "model",
            "relationships",
            "next_cursor",
            "omitted_count",
            "redaction_facts",
            "unsupported_facts",
        },
        field_name="thread read projection",
    )
    active_turn = value["active_turn"]
    return ThreadReadProjectionV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        locator=thread_locator_from_dict(_mapping(value["locator"], "locator")),
        title=_string(value["title"], "title"),
        status=_string(value["status"], "status"),
        updated_at=parse_timestamp(value["updated_at"], field_name="updated_at"),
        visible_messages=tuple(
            _message_from_dict(item)
            for item in _object_array(value["visible_messages"], "visible_messages")
        ),
        active_turn=(
            None
            if active_turn is None
            else _active_turn_from_dict(_mapping(active_turn, "active_turn"))
        ),
        route=_optional_string(value["route"], "route"),
        model=_optional_string(value["model"], "model"),
        relationships=tuple(
            _relationship_from_dict(item)
            for item in _object_array(value["relationships"], "relationships")
        ),
        next_cursor=_optional_string(value["next_cursor"], "next_cursor"),
        omitted_count=_integer(value["omitted_count"], "omitted_count"),
        redaction_facts=_string_tuple(value["redaction_facts"], "redaction_facts"),
        unsupported_facts=_string_tuple(
            value["unsupported_facts"], "unsupported_facts"
        ),
    )


def thread_message_envelope_to_dict(value: ThreadMessageEnvelopeV1) -> dict[str, Any]:
    """Serialize one authority-bound relay envelope."""
    return {
        "schema_version": value.schema_version,
        "source_locator": (
            thread_locator_to_dict(value.source_locator)
            if value.source_locator is not None
            else None
        ),
        "target_locator": thread_locator_to_dict(value.target_locator),
        "actor_binding": value.actor_binding,
        "project_binding": value.project_binding,
        "role": value.role.value,
        "author_mode": value.author_mode.value,
        "message_ref": value.message_ref,
        "attachment_refs": list(value.attachment_refs),
        "intent": value.intent.value,
        "expected_target_revision": value.expected_target_revision,
        "expected_active_turn_id": value.expected_active_turn_id,
        "idempotency_key": value.idempotency_key,
        "expires_at": value.expires_at.isoformat(),
        "depth": value.depth,
    }


def thread_message_envelope_from_dict(
    payload: Mapping[str, Any],
) -> ThreadMessageEnvelopeV1:
    """Decode one strict authority-bound relay envelope."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "source_locator",
            "target_locator",
            "actor_binding",
            "project_binding",
            "role",
            "author_mode",
            "message_ref",
            "attachment_refs",
            "intent",
            "expected_target_revision",
            "expected_active_turn_id",
            "idempotency_key",
            "expires_at",
            "depth",
        },
        field_name="thread message envelope",
    )
    source = value["source_locator"]
    return ThreadMessageEnvelopeV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        source_locator=(
            None
            if source is None
            else thread_locator_from_dict(_mapping(source, "source_locator"))
        ),
        target_locator=thread_locator_from_dict(
            _mapping(value["target_locator"], "target_locator")
        ),
        actor_binding=_string(value["actor_binding"], "actor_binding"),
        project_binding=_string(value["project_binding"], "project_binding"),
        role=_enum(ThreadVisibleRole, value["role"], "role"),
        author_mode=_enum(ThreadAuthorMode, value["author_mode"], "author_mode"),
        message_ref=_string(value["message_ref"], "message_ref"),
        attachment_refs=_string_tuple(value["attachment_refs"], "attachment_refs"),
        intent=_enum(ThreadDeliveryIntent, value["intent"], "intent"),
        expected_target_revision=_string(
            value["expected_target_revision"], "expected_target_revision"
        ),
        expected_active_turn_id=_optional_string(
            value["expected_active_turn_id"], "expected_active_turn_id"
        ),
        idempotency_key=_string(value["idempotency_key"], "idempotency_key"),
        expires_at=parse_timestamp(value["expires_at"], field_name="expires_at"),
        depth=_integer(value["depth"], "depth"),
    )


def thread_delivery_receipt_to_dict(value: ThreadDeliveryReceiptV1) -> dict[str, Any]:
    """Serialize one digest-only relay receipt."""
    return {
        "schema_version": value.schema_version,
        "delivery_id": value.delivery_id,
        "source_identity": (
            thread_locator_to_dict(value.source_identity)
            if value.source_identity is not None
            else None
        ),
        "target_identity": thread_locator_to_dict(value.target_identity),
        "action": value.action.value,
        "status": value.status.value,
        "created_at": value.created_at.isoformat(),
        "accepted_at": (
            value.accepted_at.isoformat() if value.accepted_at is not None else None
        ),
        "completed_at": (
            value.completed_at.isoformat() if value.completed_at is not None else None
        ),
        "run_ref": value.run_ref,
        "job_ref": value.job_ref,
        "turn_ref": value.turn_ref,
        "content_digest": value.content_digest,
        "capability_revision": value.capability_revision,
        "terminal_reason": value.terminal_reason,
    }


def thread_delivery_receipt_from_dict(
    payload: Mapping[str, Any],
) -> ThreadDeliveryReceiptV1:
    """Decode one strict digest-only relay receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "delivery_id",
            "source_identity",
            "target_identity",
            "action",
            "status",
            "created_at",
            "accepted_at",
            "completed_at",
            "run_ref",
            "job_ref",
            "turn_ref",
            "content_digest",
            "capability_revision",
            "terminal_reason",
        },
        field_name="thread delivery receipt",
    )
    source = value["source_identity"]
    return ThreadDeliveryReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        delivery_id=_string(value["delivery_id"], "delivery_id"),
        source_identity=(
            None
            if source is None
            else thread_locator_from_dict(_mapping(source, "source_identity"))
        ),
        target_identity=thread_locator_from_dict(
            _mapping(value["target_identity"], "target_identity")
        ),
        action=_enum(ThreadDeliveryIntent, value["action"], "action"),
        status=_enum(ThreadDeliveryStatus, value["status"], "status"),
        created_at=parse_timestamp(value["created_at"], field_name="created_at"),
        accepted_at=_optional_timestamp(value["accepted_at"], "accepted_at"),
        completed_at=_optional_timestamp(value["completed_at"], "completed_at"),
        run_ref=_optional_string(value["run_ref"], "run_ref"),
        job_ref=_optional_string(value["job_ref"], "job_ref"),
        turn_ref=_optional_string(value["turn_ref"], "turn_ref"),
        content_digest=_string(value["content_digest"], "content_digest"),
        capability_revision=_string(
            value["capability_revision"], "capability_revision"
        ),
        terminal_reason=_optional_string(value["terminal_reason"], "terminal_reason"),
    )


def _message_to_dict(value: ThreadVisibleMessageV1) -> dict[str, Any]:
    return {
        "schema_version": value.schema_version,
        "message_id": value.message_id,
        "role": value.role.value,
        "content": value.content,
        "content_digest": value.content_digest,
        "created_at": value.created_at.isoformat(),
        "redacted": value.redacted,
    }


def _message_from_dict(payload: Mapping[str, Any]) -> ThreadVisibleMessageV1:
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "message_id",
            "role",
            "content",
            "content_digest",
            "created_at",
            "redacted",
        },
        field_name="thread visible message",
    )
    return ThreadVisibleMessageV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        message_id=_string(value["message_id"], "message_id"),
        role=_enum(ThreadVisibleRole, value["role"], "role"),
        content=_string(value["content"], "content"),
        content_digest=_string(value["content_digest"], "content_digest"),
        created_at=parse_timestamp(value["created_at"], field_name="created_at"),
        redacted=_boolean(value["redacted"], "redacted"),
    )


def _active_turn_to_dict(value: ThreadActiveTurnV1) -> dict[str, Any]:
    return {
        "schema_version": value.schema_version,
        "turn_id": value.turn_id,
        "status": value.status,
        "revision": value.revision,
    }


def _active_turn_from_dict(payload: Mapping[str, Any]) -> ThreadActiveTurnV1:
    value = require_mapping(
        payload,
        required={"schema_version", "turn_id", "status", "revision"},
        field_name="thread active turn",
    )
    return ThreadActiveTurnV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        turn_id=_string(value["turn_id"], "turn_id"),
        status=_string(value["status"], "status"),
        revision=_string(value["revision"], "revision"),
    )


def _relationship_to_dict(value: ThreadRelationshipV1) -> dict[str, Any]:
    return {
        "schema_version": value.schema_version,
        "kind": value.kind.value,
        "locator": thread_locator_to_dict(value.locator),
    }


def _relationship_from_dict(payload: Mapping[str, Any]) -> ThreadRelationshipV1:
    value = require_mapping(
        payload,
        required={"schema_version", "kind", "locator"},
        field_name="thread relationship",
    )
    return ThreadRelationshipV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        kind=_enum(ThreadRelationshipKind, value["kind"], "kind"),
        locator=thread_locator_from_dict(_mapping(value["locator"], "locator")),
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return cast(Mapping[str, Any], value)


def _object_array(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of objects")
    return tuple(cast(Mapping[str, Any], item) for item in value)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(cast(str, item) for item in value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _optional_timestamp(value: object, field_name: str):
    if value is None:
        return None
    return parse_timestamp(value, field_name=field_name)
