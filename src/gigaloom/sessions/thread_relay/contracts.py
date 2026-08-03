"""Bounded, authority-bound Thread Relay wire contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypeVar

from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    canonical_digest,
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_schema_version,
    validate_text,
    validate_timestamp,
)


THREAD_RELAY_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION
MAX_THREAD_VISIBLE_MESSAGES = 100
MAX_THREAD_MESSAGE_CHARS = 16_384
MAX_THREAD_VISIBLE_CONTENT_CHARS = 64 * 1024
MAX_THREAD_RELATIONSHIPS = 64
MAX_THREAD_ATTACHMENT_REFS = 16
MAX_THREAD_FACTS = 64
MAX_THREAD_CURSOR_CHARS = 1_024
MAX_THREAD_RELAY_DEPTH = 1
MAX_THREAD_OUTSTANDING_CHILDREN = 4


class ThreadSourceKind(str, Enum):
    """Supported bounded thread sources."""

    GIGALOOM = "gigaloom"
    CODEX = "codex"
    ACP = "acp"


class ThreadVisibleRole(str, Enum):
    """Visible roles admitted to a bounded read projection."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ThreadRelationshipKind(str, Enum):
    """Content-free relationships between thread identities."""

    PARENT = "parent"
    SIBLING = "sibling"
    LINKED = "linked"


class ThreadAuthorMode(str, Enum):
    """Authority source for a user-role delivery."""

    USER_AUTHORED = "user_authored"
    AGENT_PROPOSED_USER_APPROVED = "agent_proposed_user_approved"


class ThreadDeliveryIntent(str, Enum):
    """Explicit mutation requested against the target thread."""

    MESSAGE = "message"
    FOLLOW_UP = "follow_up"
    STEER = "steer"


class ThreadDeliveryStatus(str, Enum):
    """Immutable receipt lifecycle state."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ThreadLocatorV1:
    """Actor- and project-scoped locator for one supported thread source."""

    source_kind: ThreadSourceKind
    adapter_id: str
    project_id: str
    thread_id: str
    actor_scope: str
    workspace_identity: str | None
    provider_session_ref: str | None
    capability_revision: str
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread locator")
        if not isinstance(self.source_kind, ThreadSourceKind):
            raise ValueError("thread source kind is invalid")
        for value, label in (
            (self.adapter_id, "thread adapter id"),
            (self.project_id, "thread project id"),
            (self.thread_id, "thread id"),
            (self.actor_scope, "thread actor scope"),
            (self.capability_revision, "thread capability revision"),
        ):
            validate_identity(value, field_name=label)
        for value, label in (
            (self.workspace_identity, "thread workspace identity"),
            (self.provider_session_ref, "thread provider session reference"),
        ):
            if value is not None:
                validate_identity(value, field_name=label)


@dataclass(frozen=True, slots=True)
class ThreadVisibleMessageV1:
    """One redacted visible message; system and hidden reasoning are excluded."""

    message_id: str
    role: ThreadVisibleRole
    content: str
    content_digest: str
    created_at: datetime
    redacted: bool
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread message")
        validate_identity(self.message_id, field_name="thread message id")
        if not isinstance(self.role, ThreadVisibleRole):
            raise ValueError("thread visible message role is invalid")
        _validate_message_content(self.content)
        validate_digest(self.content_digest, field_name="thread message content digest")
        if self.content_digest != thread_message_content_digest(self.content):
            raise ValueError("thread message content digest does not match content")
        validate_timestamp(self.created_at, field_name="thread message created_at")
        if not isinstance(self.redacted, bool):
            raise ValueError("thread message redacted flag must be boolean")


@dataclass(frozen=True, slots=True)
class ThreadActiveTurnV1:
    """Current active-turn fact required for exact steering."""

    turn_id: str
    status: str
    revision: str
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread active turn")
        validate_identity(self.turn_id, field_name="thread active turn id")
        validate_identity(self.status, field_name="thread active turn status")
        validate_identity(self.revision, field_name="thread active turn revision")


@dataclass(frozen=True, slots=True)
class ThreadRelationshipV1:
    """Content-free relationship to another permitted thread."""

    kind: ThreadRelationshipKind
    locator: ThreadLocatorV1
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread relationship")
        if not isinstance(self.kind, ThreadRelationshipKind):
            raise ValueError("thread relationship kind is invalid")
        if not isinstance(self.locator, ThreadLocatorV1):
            raise ValueError("thread relationship locator is invalid")


@dataclass(frozen=True, slots=True)
class ThreadReadProjectionV1:
    """Bounded, redaction-aware visible projection of one permitted thread."""

    locator: ThreadLocatorV1
    title: str
    status: str
    updated_at: datetime
    visible_messages: tuple[ThreadVisibleMessageV1, ...]
    active_turn: ThreadActiveTurnV1 | None
    route: str | None
    model: str | None
    relationships: tuple[ThreadRelationshipV1, ...]
    next_cursor: str | None
    omitted_count: int
    redaction_facts: tuple[str, ...]
    unsupported_facts: tuple[str, ...]
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread projection")
        if not isinstance(self.locator, ThreadLocatorV1):
            raise ValueError("thread projection locator is invalid")
        validate_text(self.title, field_name="thread title", max_chars=512)
        validate_identity(self.status, field_name="thread status")
        validate_timestamp(self.updated_at, field_name="thread updated_at")
        messages = _normalize_instances(
            self.visible_messages,
            ThreadVisibleMessageV1,
            field_name="thread visible messages",
            maximum=MAX_THREAD_VISIBLE_MESSAGES,
        )
        if len({message.message_id for message in messages}) != len(messages):
            raise ValueError("thread visible message ids must be unique")
        if (
            sum(len(message.content) for message in messages)
            > MAX_THREAD_VISIBLE_CONTENT_CHARS
        ):
            raise ValueError("thread visible message content exceeds the total bound")
        if tuple(message.created_at for message in messages) != tuple(
            sorted(message.created_at for message in messages)
        ):
            raise ValueError("thread visible messages must be chronological")
        object.__setattr__(self, "visible_messages", messages)
        if self.active_turn is not None and not isinstance(
            self.active_turn, ThreadActiveTurnV1
        ):
            raise ValueError("thread active turn is invalid")
        for value, label in (
            (self.route, "thread route"),
            (self.model, "thread model"),
        ):
            if value is not None:
                validate_text(value, field_name=label, max_chars=512)
        relationships = _normalize_instances(
            self.relationships,
            ThreadRelationshipV1,
            field_name="thread relationships",
            maximum=MAX_THREAD_RELATIONSHIPS,
        )
        relationship_keys = tuple(
            (item.kind.value, thread_locator_digest(item.locator))
            for item in relationships
        )
        if len(set(relationship_keys)) != len(relationship_keys):
            raise ValueError("thread relationships must be unique")
        object.__setattr__(self, "relationships", relationships)
        if self.next_cursor is not None:
            validate_text(
                self.next_cursor,
                field_name="thread next cursor",
                max_chars=MAX_THREAD_CURSOR_CHARS,
            )
        _validate_count(self.omitted_count, field_name="thread omitted count")
        redaction_facts = normalize_identities(
            self.redaction_facts,
            field_name="thread redaction facts",
            maximum=MAX_THREAD_FACTS,
        )
        unsupported_facts = normalize_identities(
            self.unsupported_facts,
            field_name="thread unsupported facts",
            maximum=MAX_THREAD_FACTS,
        )
        if any(message.redacted for message in messages) and not redaction_facts:
            raise ValueError("redacted thread messages require redaction facts")
        object.__setattr__(self, "redaction_facts", redaction_facts)
        object.__setattr__(self, "unsupported_facts", unsupported_facts)


@dataclass(frozen=True, slots=True)
class ThreadMessageEnvelopeV1:
    """Bounded user-role mutation admitted against an exact target revision."""

    source_locator: ThreadLocatorV1 | None
    target_locator: ThreadLocatorV1
    actor_binding: str
    project_binding: str
    role: ThreadVisibleRole
    author_mode: ThreadAuthorMode
    message_ref: str
    attachment_refs: tuple[str, ...]
    intent: ThreadDeliveryIntent
    expected_target_revision: str
    expected_active_turn_id: str | None
    idempotency_key: str
    expires_at: datetime
    depth: int
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread envelope")
        if self.source_locator is not None and not isinstance(
            self.source_locator, ThreadLocatorV1
        ):
            raise ValueError("thread source locator is invalid")
        if not isinstance(self.target_locator, ThreadLocatorV1):
            raise ValueError("thread target locator is invalid")
        validate_identity(self.actor_binding, field_name="thread actor binding")
        validate_identity(self.project_binding, field_name="thread project binding")
        if self.actor_binding != self.target_locator.actor_scope:
            raise ValueError("thread actor binding does not match target")
        if self.project_binding != self.target_locator.project_id:
            raise ValueError("thread project binding does not match target")
        if self.source_locator is not None and (
            self.source_locator.actor_scope != self.actor_binding
            or self.source_locator.project_id != self.project_binding
        ):
            raise ValueError("cross-actor or cross-project thread delivery is denied")
        if self.role is not ThreadVisibleRole.USER:
            raise ValueError("thread delivery role must be user")
        if not isinstance(self.author_mode, ThreadAuthorMode):
            raise ValueError("thread author mode is invalid")
        validate_identity(self.message_ref, field_name="thread message reference")
        attachment_refs = normalize_identities(
            self.attachment_refs,
            field_name="thread attachment references",
            maximum=MAX_THREAD_ATTACHMENT_REFS,
            sort_values=False,
        )
        object.__setattr__(self, "attachment_refs", attachment_refs)
        if not isinstance(self.intent, ThreadDeliveryIntent):
            raise ValueError("thread delivery intent is invalid")
        validate_identity(
            self.expected_target_revision,
            field_name="thread expected target revision",
        )
        if self.intent is ThreadDeliveryIntent.STEER:
            if self.expected_active_turn_id is None:
                raise ValueError("thread steer requires the exact active turn id")
            validate_identity(
                self.expected_active_turn_id,
                field_name="thread expected active turn id",
            )
        elif self.expected_active_turn_id is not None:
            raise ValueError("only thread steer accepts an active turn id")
        validate_identity(self.idempotency_key, field_name="thread idempotency key")
        validate_timestamp(self.expires_at, field_name="thread expires_at")
        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or not 0 <= self.depth <= MAX_THREAD_RELAY_DEPTH
        ):
            raise ValueError("thread delivery depth exceeds the supported bound")


@dataclass(frozen=True, slots=True)
class ThreadDeliveryReceiptV1:
    """Content-digest-only immutable evidence for one relay delivery."""

    delivery_id: str
    source_identity: ThreadLocatorV1 | None
    target_identity: ThreadLocatorV1
    action: ThreadDeliveryIntent
    status: ThreadDeliveryStatus
    created_at: datetime
    accepted_at: datetime | None
    completed_at: datetime | None
    run_ref: str | None
    job_ref: str | None
    turn_ref: str | None
    content_digest: str
    capability_revision: str
    terminal_reason: str | None
    schema_version: int = THREAD_RELAY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="thread receipt")
        validate_identity(self.delivery_id, field_name="thread delivery id")
        if self.source_identity is not None and not isinstance(
            self.source_identity, ThreadLocatorV1
        ):
            raise ValueError("thread receipt source identity is invalid")
        if not isinstance(self.target_identity, ThreadLocatorV1):
            raise ValueError("thread receipt target identity is invalid")
        if self.source_identity is not None and (
            self.source_identity.actor_scope != self.target_identity.actor_scope
            or self.source_identity.project_id != self.target_identity.project_id
        ):
            raise ValueError("thread receipt cannot cross actor or project boundaries")
        if not isinstance(self.action, ThreadDeliveryIntent):
            raise ValueError("thread receipt action is invalid")
        if not isinstance(self.status, ThreadDeliveryStatus):
            raise ValueError("thread receipt status is invalid")
        validate_timestamp(self.created_at, field_name="thread receipt created_at")
        for value, label in (
            (self.accepted_at, "thread receipt accepted_at"),
            (self.completed_at, "thread receipt completed_at"),
        ):
            if value is not None:
                validate_timestamp(value, field_name=label)
                if value < self.created_at:
                    raise ValueError(f"{label} precedes creation")
        if (
            self.accepted_at is not None
            and self.completed_at is not None
            and self.completed_at < self.accepted_at
        ):
            raise ValueError("thread receipt completion precedes acceptance")
        for value, label in (
            (self.run_ref, "thread run reference"),
            (self.job_ref, "thread job reference"),
            (self.turn_ref, "thread turn reference"),
        ):
            if value is not None:
                validate_identity(value, field_name=label)
        validate_digest(self.content_digest, field_name="thread receipt content digest")
        validate_identity(
            self.capability_revision,
            field_name="thread receipt capability revision",
        )
        terminal = self.status in {
            ThreadDeliveryStatus.FAILED,
            ThreadDeliveryStatus.EXPIRED,
            ThreadDeliveryStatus.CANCELLED,
        }
        if self.status is ThreadDeliveryStatus.PENDING:
            if self.accepted_at is not None or self.completed_at is not None:
                raise ValueError("pending thread receipt cannot have later timestamps")
        elif self.status is ThreadDeliveryStatus.ACCEPTED:
            if self.accepted_at is None or self.completed_at is not None:
                raise ValueError("accepted thread receipt requires acceptance only")
        elif self.status is ThreadDeliveryStatus.COMPLETED:
            if self.accepted_at is None or self.completed_at is None:
                raise ValueError("completed thread receipt requires both timestamps")
        elif terminal and self.completed_at is None:
            raise ValueError("terminal thread receipt requires completion time")
        if terminal:
            if self.terminal_reason is None:
                raise ValueError("terminal thread receipt requires a reason")
            validate_identity(
                self.terminal_reason,
                field_name="thread receipt terminal reason",
            )
        elif self.terminal_reason is not None:
            raise ValueError("non-terminal thread receipt cannot have a reason")


def thread_locator_digest(value: ThreadLocatorV1) -> str:
    """Return the stable content-free identity digest for one locator."""
    return canonical_digest(_locator_payload(value))


def thread_message_content_digest(content: str) -> str:
    """Return the canonical digest for already-redacted visible message text."""
    _validate_message_content(content)
    return canonical_digest(content)


def thread_read_projection_digest(value: ThreadReadProjectionV1) -> str:
    """Return the stable digest of one bounded read projection."""
    from gigaloom.sessions.thread_relay.codec import thread_read_projection_to_dict

    return canonical_digest(thread_read_projection_to_dict(value))


def thread_message_envelope_digest(value: ThreadMessageEnvelopeV1) -> str:
    """Return the stable authority/content-reference envelope digest."""
    from gigaloom.sessions.thread_relay.codec import thread_message_envelope_to_dict

    return canonical_digest(thread_message_envelope_to_dict(value))


def thread_delivery_receipt_digest(value: ThreadDeliveryReceiptV1) -> str:
    """Return the stable content-free delivery receipt digest."""
    from gigaloom.sessions.thread_relay.codec import thread_delivery_receipt_to_dict

    return canonical_digest(thread_delivery_receipt_to_dict(value))


def _locator_payload(value: ThreadLocatorV1) -> dict[str, object]:
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


def _validate_message_content(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_THREAD_MESSAGE_CHARS:
        raise ValueError("thread message content exceeds the per-message bound")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise ValueError("thread message content contains unsupported control data")
    if "\x7f" in value:
        raise ValueError("thread message content contains unsupported control data")
    return value


def _validate_count(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} is invalid")
    return value


_InstanceT = TypeVar("_InstanceT")


def _normalize_instances(
    values: object,
    instance_type: type[_InstanceT],
    *,
    field_name: str,
    maximum: int,
) -> tuple[_InstanceT, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > maximum
        or any(not isinstance(item, instance_type) for item in values)
    ):
        raise ValueError(f"{field_name} must be a bounded tuple")
    return values
