"""Dependency-light Action Inbox models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


ACTION_INBOX_SCHEMA_VERSION = 1
ACTION_INBOX_ITEM_KIND = "gigaloom.action_inbox.item.v1"
MAX_ACTION_INBOX_ITEMS = 200
MAX_ACTION_INBOX_RESPONSE_BYTES = 16 * 1024


class ActionInboxKind(StrEnum):
    """Closed owner-backed pending-action kinds in the first release slice."""

    APPROVAL = "approval"
    AUTOMATION_QUESTION = "automation_question"
    MCP_ELICITATION = "mcp_elicitation"
    PROVIDER_LOGIN = "provider_login"
    RUN_INPUT = "run_input"


class ActionInboxCommand(StrEnum):
    """Commands dispatched to an existing authoritative owner."""

    ALLOW_ONCE = "allow_once"
    ALLOW_RUN = "allow_run"
    ALLOW_SESSION = "allow_session"
    ALLOW_PROJECT = "allow_project"
    ANSWER = "answer"
    CONTINUE = "continue"
    CANCEL = "cancel"
    DENY = "deny"


class ActionInboxStatus(StrEnum):
    """Lifecycle state projected by an action owner."""

    PENDING = "pending"
    ANSWERED = "answered"
    CANCELED = "canceled"
    EXPIRED = "expired"


class ActionConsequence(StrEnum):
    """Bounded consequence classification, never an authority grant."""

    READ_ONLY = "read_only"
    RUN_CONTROL = "run_control"
    WORKSPACE_WRITE = "workspace_write"
    EXTERNAL_WRITE = "external_write"
    AUTHENTICATION = "authentication"


@dataclass(frozen=True, slots=True)
class ActionInboxItem:
    """Content-free, digest-bound reference to one pending owner action."""

    item_id: str
    kind: ActionInboxKind
    authority: str
    owner_id: str
    workspace_id: str
    origin: str
    revision: str
    consequence: ActionConsequence
    status: ActionInboxStatus
    allowed_actions: tuple[ActionInboxCommand, ...]
    created_at: str
    item_sha256: str
    response_schema: str | None = None
    expires_at: str | None = None
    session_id: str | None = None
    run_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a detached canonical JSON-compatible item."""
        from .codec import action_inbox_item_to_dict

        return action_inbox_item_to_dict(self)

    @classmethod
    def from_dict(cls, payload: object) -> ActionInboxItem:
        """Parse and verify one exact schema-v1 Action Inbox item."""
        from .codec import action_inbox_item_from_dict

        return action_inbox_item_from_dict(payload)


@dataclass(frozen=True, slots=True)
class ActionInboxResponseRequest:
    """Optimistic, idempotent command sent through the Inbox."""

    item_id: str
    authority: str
    owner_id: str
    workspace_id: str
    expected_revision: str
    expected_item_sha256: str
    action: ActionInboxCommand
    idempotency_key: str
    response: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionInboxResponseResult:
    """Content-free owner receipt for an answered action."""

    response_id: str
    item_id: str
    authority: str
    owner_id: str
    workspace_id: str
    action: ActionInboxCommand
    status: ActionInboxStatus
    revision: str
    receipt_sha256: str
    idempotent_replay: bool = False


@dataclass(frozen=True, slots=True)
class ActionInboxSnapshot:
    """Bounded cross-owner pending-item snapshot."""

    owner_id: str
    workspace_id: str
    items: tuple[ActionInboxItem, ...]
    snapshot_sha256: str
