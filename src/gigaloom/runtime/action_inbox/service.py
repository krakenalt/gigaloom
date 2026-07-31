"""Cross-owner Action Inbox projection and guarded response dispatch."""

from __future__ import annotations

from typing import Protocol

from .codec import (
    action_inbox_item_from_dict,
    snapshot_digest,
    validate_action_inbox_authority,
    validate_action_inbox_scope,
    validate_response_request,
    validate_response_result,
)
from .models import (
    MAX_ACTION_INBOX_ITEMS,
    ActionInboxItem,
    ActionInboxResponseRequest,
    ActionInboxResponseResult,
    ActionInboxSnapshot,
    ActionInboxStatus,
)


class ActionInboxError(RuntimeError):
    """Base error for the typed Action Inbox boundary."""


class ActionInboxNotFoundError(ActionInboxError):
    """The named owner or item does not exist."""


class ActionInboxForbiddenError(ActionInboxError):
    """The item belongs to another owner or workspace."""


class ActionInboxConflictError(ActionInboxError):
    """The item changed or is no longer pending."""


class ActionInboxValidationError(ActionInboxError):
    """The requested action or owner result violates the public contract."""


class ActionInboxOwnerPort(Protocol):
    """Authoritative owner adapter; facts and idempotency remain with the owner."""

    authority: str

    def list_pending(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int,
    ) -> tuple[object, ...]: ...

    def get_item(self, item_id: str) -> object: ...

    def replay_response(
        self,
        request: ActionInboxResponseRequest,
    ) -> ActionInboxResponseResult | None: ...

    def respond(
        self,
        request: ActionInboxResponseRequest,
    ) -> ActionInboxResponseResult: ...


class ActionInboxService:
    """Merge bounded owner projections and dispatch conflict-safe responses."""

    def __init__(self, owners: tuple[ActionInboxOwnerPort, ...]) -> None:
        self._owners: dict[str, ActionInboxOwnerPort] = {}
        for owner in owners:
            authority = validate_action_inbox_authority(owner.authority)
            if authority in self._owners:
                raise ValueError("action inbox owner authorities must be unique")
            self._owners[authority] = owner

    def snapshot(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int = MAX_ACTION_INBOX_ITEMS,
    ) -> ActionInboxSnapshot:
        """Return one deterministic bounded snapshot across registered owners."""
        if isinstance(limit, bool) or not 1 <= limit <= MAX_ACTION_INBOX_ITEMS:
            raise ValueError("action inbox limit is outside the supported range")
        owner_id, workspace_id = validate_action_inbox_scope(owner_id, workspace_id)
        items = []
        for authority, port in sorted(self._owners.items()):
            projected = port.list_pending(
                owner_id=owner_id,
                workspace_id=workspace_id,
                limit=limit,
            )
            if len(projected) > limit:
                raise ActionInboxValidationError(
                    f"action inbox owner {authority} exceeded its item limit"
                )
            for raw_item in projected:
                item = _verified_item(raw_item)
                if item.authority != authority:
                    raise ActionInboxValidationError(
                        "action inbox item authority does not match its owner"
                    )
                _require_binding(
                    item.owner_id, item.workspace_id, owner_id, workspace_id
                )
                if item.status is not ActionInboxStatus.PENDING:
                    raise ActionInboxValidationError(
                        "action inbox owner returned a non-pending item"
                    )
                items.append(item)
        ordered = tuple(
            sorted(
                items,
                key=lambda item: (
                    item.created_at,
                    item.authority,
                    item.item_id,
                ),
                reverse=True,
            )[:limit]
        )
        return ActionInboxSnapshot(
            owner_id=owner_id,
            workspace_id=workspace_id,
            items=ordered,
            snapshot_sha256=snapshot_digest(ordered),
        )

    def respond(
        self,
        request: ActionInboxResponseRequest,
    ) -> ActionInboxResponseResult:
        """Validate optimistic bindings before calling the authoritative owner."""
        try:
            checked = validate_response_request(request)
        except ValueError as exc:
            raise ActionInboxValidationError(str(exc)) from exc
        port = self._owners.get(checked.authority)
        if port is None:
            raise ActionInboxNotFoundError("action inbox owner was not found")
        try:
            replay = port.replay_response(checked)
        except KeyError as exc:
            raise ActionInboxNotFoundError("action inbox item was not found") from exc
        if replay is not None:
            result = _verified_result(replay)
            _require_result_matches(result, checked, idempotent_replay=True)
            return result
        try:
            item = _verified_item(port.get_item(checked.item_id))
        except KeyError as exc:
            raise ActionInboxNotFoundError("action inbox item was not found") from exc
        if item.authority != checked.authority or item.item_id != checked.item_id:
            raise ActionInboxValidationError(
                "action inbox owner returned the wrong item"
            )
        _require_binding(
            item.owner_id,
            item.workspace_id,
            checked.owner_id,
            checked.workspace_id,
        )
        if item.status is not ActionInboxStatus.PENDING:
            raise ActionInboxConflictError("action inbox item is no longer pending")
        if (
            item.revision != checked.expected_revision
            or item.item_sha256 != checked.expected_item_sha256
        ):
            raise ActionInboxConflictError(
                "action inbox item changed; resnapshot required"
            )
        if checked.action not in item.allowed_actions:
            raise ActionInboxValidationError(
                "action is not allowed by the current action inbox item"
            )
        if checked.action.value == "answer" and item.response_schema is None:
            raise ActionInboxValidationError("action inbox item is not answerable")
        result = _verified_result(port.respond(checked))
        _require_result_matches(result, checked, idempotent_replay=False)
        return result


def _verified_item(value: object) -> ActionInboxItem:
    try:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return action_inbox_item_from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ActionInboxValidationError(str(exc)) from exc


def _verified_result(value: ActionInboxResponseResult) -> ActionInboxResponseResult:
    try:
        return validate_response_result(value)
    except ValueError as exc:
        raise ActionInboxValidationError(str(exc)) from exc


def _require_result_matches(
    result: ActionInboxResponseResult,
    request: ActionInboxResponseRequest,
    *,
    idempotent_replay: bool,
) -> None:
    _require_binding(
        result.owner_id,
        result.workspace_id,
        request.owner_id,
        request.workspace_id,
    )
    expected = (
        result.item_id == request.item_id
        and result.authority == request.authority
        and result.action is request.action
    )
    if not expected:
        raise ActionInboxValidationError(
            "action inbox owner returned a mismatched response receipt"
        )
    if result.idempotent_replay is not idempotent_replay:
        raise ActionInboxValidationError(
            "action inbox owner returned an invalid idempotency state"
        )


def _require_binding(
    actual_owner_id: str,
    actual_workspace_id: str,
    expected_owner_id: str,
    expected_workspace_id: str,
) -> None:
    if actual_owner_id != expected_owner_id:
        raise ActionInboxForbiddenError("action inbox owner binding does not match")
    if actual_workspace_id != expected_workspace_id:
        raise ActionInboxForbiddenError("action inbox workspace binding does not match")
