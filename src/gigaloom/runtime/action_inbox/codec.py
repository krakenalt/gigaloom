"""Canonical Action Inbox item and response validation."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Mapping, cast

from .models import (
    ACTION_INBOX_ITEM_KIND,
    ACTION_INBOX_SCHEMA_VERSION,
    MAX_ACTION_INBOX_RESPONSE_BYTES,
    ActionConsequence,
    ActionInboxCommand,
    ActionInboxItem,
    ActionInboxKind,
    ActionInboxResponseRequest,
    ActionInboxResponseResult,
    ActionInboxStatus,
)


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@~-]{0,255}\Z")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9._-]{0,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_-])(?:authorization|cookie|credential|oauth|password|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "wire_kind",
        "item_id",
        "kind",
        "authority",
        "owner_id",
        "workspace_id",
        "origin",
        "revision",
        "consequence",
        "status",
        "allowed_actions",
        "response_schema",
        "created_at",
        "expires_at",
        "session_id",
        "run_id",
        "item_sha256",
    }
)
_ALLOWED_ACTIONS = {
    ActionInboxKind.APPROVAL: frozenset(
        {
            ActionInboxCommand.ALLOW_ONCE,
            ActionInboxCommand.ALLOW_RUN,
            ActionInboxCommand.ALLOW_SESSION,
            ActionInboxCommand.ALLOW_PROJECT,
            ActionInboxCommand.DENY,
        }
    ),
    ActionInboxKind.AUTOMATION_QUESTION: frozenset(
        {ActionInboxCommand.ANSWER, ActionInboxCommand.CANCEL}
    ),
    ActionInboxKind.MCP_ELICITATION: frozenset(
        {ActionInboxCommand.ANSWER, ActionInboxCommand.CANCEL}
    ),
    ActionInboxKind.PROVIDER_LOGIN: frozenset(
        {ActionInboxCommand.CONTINUE, ActionInboxCommand.CANCEL}
    ),
    ActionInboxKind.RUN_INPUT: frozenset(
        {ActionInboxCommand.ANSWER, ActionInboxCommand.CANCEL}
    ),
}


def build_action_inbox_item(
    *,
    item_id: str,
    kind: ActionInboxKind,
    authority: str,
    owner_id: str,
    workspace_id: str,
    origin: str,
    revision: str,
    consequence: ActionConsequence,
    status: ActionInboxStatus,
    allowed_actions: tuple[ActionInboxCommand, ...],
    created_at: str,
    response_schema: str | None = None,
    expires_at: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
) -> ActionInboxItem:
    """Build one canonical item containing no prompt, form, or credential data."""
    parsed_kind = ActionInboxKind(kind)
    parsed_actions = tuple(sorted(set(allowed_actions), key=lambda item: item.value))
    if not parsed_actions:
        raise ValueError("action inbox item must expose at least one action")
    if len(parsed_actions) != len(allowed_actions):
        raise ValueError("action inbox actions must be unique")
    if not set(parsed_actions) <= _ALLOWED_ACTIONS[parsed_kind]:
        raise ValueError("action inbox item contains an invalid action for its kind")
    parsed_schema = (
        _required_token(response_schema, "response_schema")
        if response_schema is not None
        else None
    )
    if ActionInboxCommand.ANSWER in parsed_actions and parsed_schema is None:
        raise ValueError("answerable action inbox item requires response_schema")
    if ActionInboxCommand.ANSWER not in parsed_actions and parsed_schema is not None:
        raise ValueError("response_schema is only valid for answerable items")

    item = ActionInboxItem(
        item_id=_required_identity(item_id, "item_id"),
        kind=parsed_kind,
        authority=_required_token(authority, "authority"),
        owner_id=_required_identity(owner_id, "owner_id"),
        workspace_id=_required_identity(workspace_id, "workspace_id"),
        origin=_required_token(origin, "origin"),
        revision=_required_identity(revision, "revision"),
        consequence=ActionConsequence(consequence),
        status=ActionInboxStatus(status),
        allowed_actions=parsed_actions,
        response_schema=parsed_schema,
        created_at=_required_timestamp(created_at, "created_at"),
        expires_at=(
            _required_timestamp(expires_at, "expires_at")
            if expires_at is not None
            else None
        ),
        session_id=_optional_identity(session_id, "session_id"),
        run_id=_optional_identity(run_id, "run_id"),
        item_sha256="",
    )
    body = _item_body(item)
    return ActionInboxItem(
        item_id=item.item_id,
        kind=item.kind,
        authority=item.authority,
        owner_id=item.owner_id,
        workspace_id=item.workspace_id,
        origin=item.origin,
        revision=item.revision,
        consequence=item.consequence,
        status=item.status,
        allowed_actions=item.allowed_actions,
        response_schema=item.response_schema,
        created_at=item.created_at,
        expires_at=item.expires_at,
        session_id=item.session_id,
        run_id=item.run_id,
        item_sha256=_json_hash(body),
    )


def action_inbox_item_to_dict(item: ActionInboxItem) -> dict[str, object]:
    """Serialize one canonical item."""
    return {**_item_body(item), "item_sha256": item.item_sha256}


def action_inbox_item_from_dict(payload: object) -> ActionInboxItem:
    """Parse one exact item and reject digest or derived-shape tampering."""
    data = _required_mapping(payload, "action inbox item")
    if frozenset(data) != _ITEM_FIELDS:
        raise ValueError("action inbox item fields are invalid")
    if data.get("schema_version") != ACTION_INBOX_SCHEMA_VERSION:
        raise ValueError("unsupported action inbox schema_version")
    if data.get("wire_kind") != ACTION_INBOX_ITEM_KIND:
        raise ValueError("action inbox wire kind is invalid")
    actions = _required_list(data.get("allowed_actions"), "allowed_actions")
    rebuilt = build_action_inbox_item(
        item_id=_required_string(data.get("item_id"), "item_id"),
        kind=ActionInboxKind(_required_string(data.get("kind"), "kind")),
        authority=_required_string(data.get("authority"), "authority"),
        owner_id=_required_string(data.get("owner_id"), "owner_id"),
        workspace_id=_required_string(data.get("workspace_id"), "workspace_id"),
        origin=_required_string(data.get("origin"), "origin"),
        revision=_required_string(data.get("revision"), "revision"),
        consequence=ActionConsequence(
            _required_string(data.get("consequence"), "consequence")
        ),
        status=ActionInboxStatus(_required_string(data.get("status"), "status")),
        allowed_actions=tuple(
            ActionInboxCommand(_required_string(item, "allowed action"))
            for item in actions
        ),
        response_schema=_optional_string(
            data.get("response_schema"), "response_schema"
        ),
        created_at=_required_string(data.get("created_at"), "created_at"),
        expires_at=_optional_string(data.get("expires_at"), "expires_at"),
        session_id=_optional_string(data.get("session_id"), "session_id"),
        run_id=_optional_string(data.get("run_id"), "run_id"),
    )
    declared = _required_sha256(data.get("item_sha256"), "item_sha256")
    if rebuilt.item_sha256 != declared:
        raise ValueError("action inbox item digest does not match")
    return rebuilt


def validate_response_request(
    request: ActionInboxResponseRequest,
) -> ActionInboxResponseRequest:
    """Validate one bounded response without persisting or logging its input."""
    action = ActionInboxCommand(request.action)
    response = _safe_response_payload(request.response)
    if action is ActionInboxCommand.ANSWER and not response:
        raise ValueError("answer action requires a response")
    if action is not ActionInboxCommand.ANSWER and response:
        raise ValueError("only answer action accepts a response")
    return ActionInboxResponseRequest(
        item_id=_required_identity(request.item_id, "item_id"),
        authority=_required_token(request.authority, "authority"),
        owner_id=_required_identity(request.owner_id, "owner_id"),
        workspace_id=_required_identity(request.workspace_id, "workspace_id"),
        expected_revision=_required_identity(
            request.expected_revision,
            "expected_revision",
        ),
        expected_item_sha256=_required_sha256(
            request.expected_item_sha256,
            "expected_item_sha256",
        ),
        action=action,
        idempotency_key=_required_identity(
            request.idempotency_key,
            "idempotency_key",
        ),
        response=response,
    )


def validate_response_result(
    result: ActionInboxResponseResult,
) -> ActionInboxResponseResult:
    """Validate the content-free receipt returned by an authoritative owner."""
    status = ActionInboxStatus(result.status)
    if status is ActionInboxStatus.PENDING:
        raise ValueError("action inbox response result must be terminal")
    return ActionInboxResponseResult(
        response_id=_required_identity(result.response_id, "response_id"),
        item_id=_required_identity(result.item_id, "item_id"),
        authority=_required_token(result.authority, "authority"),
        owner_id=_required_identity(result.owner_id, "owner_id"),
        workspace_id=_required_identity(result.workspace_id, "workspace_id"),
        action=ActionInboxCommand(result.action),
        status=status,
        revision=_required_identity(result.revision, "revision"),
        receipt_sha256=_required_sha256(result.receipt_sha256, "receipt_sha256"),
        idempotent_replay=bool(result.idempotent_replay),
    )


def snapshot_digest(items: tuple[ActionInboxItem, ...]) -> str:
    """Bind one sorted cross-owner snapshot by item identity and digest."""
    return _json_hash(
        {
            "schema_version": ACTION_INBOX_SCHEMA_VERSION,
            "items": [
                {
                    "authority": item.authority,
                    "item_id": item.item_id,
                    "item_sha256": item.item_sha256,
                }
                for item in items
            ],
        }
    )


def validate_action_inbox_scope(
    owner_id: object, workspace_id: object
) -> tuple[str, str]:
    """Validate one owner/workspace query boundary."""
    return (
        _required_identity(owner_id, "owner_id"),
        _required_identity(workspace_id, "workspace_id"),
    )


def validate_action_inbox_authority(value: object) -> str:
    """Validate one authoritative adapter name."""
    return _required_token(value, "authority")


def _item_body(item: ActionInboxItem) -> dict[str, object]:
    return {
        "schema_version": ACTION_INBOX_SCHEMA_VERSION,
        "wire_kind": ACTION_INBOX_ITEM_KIND,
        "item_id": item.item_id,
        "kind": item.kind.value,
        "authority": item.authority,
        "owner_id": item.owner_id,
        "workspace_id": item.workspace_id,
        "origin": item.origin,
        "revision": item.revision,
        "consequence": item.consequence.value,
        "status": item.status.value,
        "allowed_actions": [item.value for item in item.allowed_actions],
        "response_schema": item.response_schema,
        "created_at": item.created_at,
        "expires_at": item.expires_at,
        "session_id": item.session_id,
        "run_id": item.run_id,
    }


def _safe_response_payload(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("action inbox response must be an object")
    prepared = _safe_json_object(value, depth=0)
    try:
        encoded = json.dumps(
            prepared,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("action inbox response must be finite JSON") from exc
    if len(encoded) > MAX_ACTION_INBOX_RESPONSE_BYTES:
        raise ValueError("action inbox response byte limit exceeded")
    return prepared


def _safe_json_object(
    value: Mapping[object, object],
    *,
    depth: int,
) -> dict[str, object]:
    if depth > 5 or len(value) > 50:
        raise ValueError("action inbox response structure is too large")
    prepared: dict[str, object] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not _TOKEN_RE.fullmatch(raw_key):
            raise ValueError("action inbox response field is invalid")
        if _SENSITIVE_KEY_RE.search(raw_key):
            raise ValueError(
                "secret-bearing action inbox response fields are forbidden"
            )
        prepared[raw_key] = _safe_json_value(raw_value, depth=depth + 1)
    return prepared


def _safe_json_value(value: object, *, depth: int) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("action inbox response numbers must be finite")
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > 4096:
            raise ValueError("action inbox response string is too large")
        return value
    if isinstance(value, Mapping):
        return _safe_json_object(value, depth=depth)
    if isinstance(value, (list, tuple)):
        if depth > 5 or len(value) > 100:
            raise ValueError("action inbox response structure is too large")
        return [_safe_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("action inbox response contains a non-JSON value")


def _required_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} fields must be strings")
    return cast(Mapping[str, Any], value)


def _required_list(value: object, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, name)


def _required_identity(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _optional_identity(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _required_identity(value, name)


def _required_token(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _TOKEN_RE.fullmatch(text):
        raise ValueError(f"{name} is invalid")
    return text


def _required_sha256(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not _SHA256_RE.fullmatch(text):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return text


def _required_timestamp(value: object, name: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return text


def _json_hash(value: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
