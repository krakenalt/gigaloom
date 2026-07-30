"""Typed cross-run Action Inbox contracts and dispatch tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
from typing import Mapping

import pytest

from gigaloom.runtime.action_inbox.api import (
    MAX_ACTION_INBOX_ITEMS,
    ActionConsequence,
    ActionInboxCommand,
    ActionInboxConflictError,
    ActionInboxForbiddenError,
    ActionInboxItem,
    ActionInboxKind,
    ActionInboxOwnerPort,
    ActionInboxResponseRequest,
    ActionInboxResponseResult,
    ActionInboxService,
    ActionInboxStatus,
    ActionInboxValidationError,
    build_action_inbox_item,
)


OWNER_ID = "owner-1"
WORKSPACE_ID = "workspace-1"
CREATED_AT = "2026-07-30T12:00:00Z"

_KIND_ACTIONS = {
    ActionInboxKind.APPROVAL: (
        ActionInboxCommand.ALLOW_ONCE,
        ActionInboxCommand.DENY,
    ),
    ActionInboxKind.AUTOMATION_QUESTION: (
        ActionInboxCommand.ANSWER,
        ActionInboxCommand.CANCEL,
    ),
    ActionInboxKind.MCP_ELICITATION: (
        ActionInboxCommand.ANSWER,
        ActionInboxCommand.CANCEL,
    ),
    ActionInboxKind.PROVIDER_LOGIN: (
        ActionInboxCommand.CONTINUE,
        ActionInboxCommand.CANCEL,
    ),
    ActionInboxKind.RUN_INPUT: (
        ActionInboxCommand.ANSWER,
        ActionInboxCommand.CANCEL,
    ),
}


def _item(
    kind: ActionInboxKind,
    *,
    authority: str | None = None,
    item_id: str | None = None,
    owner_id: str = OWNER_ID,
    workspace_id: str = WORKSPACE_ID,
    status: ActionInboxStatus = ActionInboxStatus.PENDING,
    created_at: str = CREATED_AT,
) -> ActionInboxItem:
    actions = _KIND_ACTIONS[kind]
    return build_action_inbox_item(
        item_id=item_id or f"{kind.value}-1",
        kind=kind,
        authority=authority or f"owner.{kind.value}",
        owner_id=owner_id,
        workspace_id=workspace_id,
        origin="manual",
        revision=f"{kind.value}-rev-1",
        consequence=(
            ActionConsequence.AUTHENTICATION
            if kind is ActionInboxKind.PROVIDER_LOGIN
            else ActionConsequence.RUN_CONTROL
        ),
        status=status,
        allowed_actions=actions,
        response_schema=(
            f"{kind.value}.answer.v1" if ActionInboxCommand.ANSWER in actions else None
        ),
        created_at=created_at,
        expires_at="2026-07-31T12:00:00Z",
        session_id="session-1",
        run_id="run-1",
    )


class _Owner(ActionInboxOwnerPort):
    def __init__(self, authority: str, items: tuple[ActionInboxItem, ...]) -> None:
        self.authority = authority
        self.items = {item.item_id: item for item in items}
        self.responses: dict[
            str, tuple[ActionInboxResponseRequest, ActionInboxResponseResult]
        ] = {}
        self.respond_calls = 0

    def list_pending(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        limit: int,
    ) -> tuple[object, ...]:
        del owner_id, workspace_id
        return tuple(self.items.values())[:limit]

    def get_item(self, item_id: str) -> object:
        return self.items[item_id]

    def replay_response(
        self,
        request: ActionInboxResponseRequest,
    ) -> ActionInboxResponseResult | None:
        replay = self.responses.get(request.idempotency_key)
        if replay is None:
            return None
        original, result = replay
        if original != request:
            raise ActionInboxConflictError("idempotency key was reused")
        return replace(result, idempotent_replay=True)

    def respond(
        self,
        request: ActionInboxResponseRequest,
    ) -> ActionInboxResponseResult:
        self.respond_calls += 1
        status = (
            ActionInboxStatus.CANCELED
            if request.action is ActionInboxCommand.CANCEL
            else ActionInboxStatus.ANSWERED
        )
        receipt = ActionInboxResponseResult(
            response_id=f"response-{request.item_id}",
            item_id=request.item_id,
            authority=request.authority,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            action=request.action,
            status=status,
            revision="response-rev-1",
            receipt_sha256=hashlib.sha256(
                f"{request.item_id}:{request.idempotency_key}".encode()
            ).hexdigest(),
        )
        self.responses[request.idempotency_key] = (request, receipt)
        current = self.items[request.item_id]
        self.items[request.item_id] = build_action_inbox_item(
            item_id=current.item_id,
            kind=current.kind,
            authority=current.authority,
            owner_id=current.owner_id,
            workspace_id=current.workspace_id,
            origin=current.origin,
            revision="item-rev-2",
            consequence=current.consequence,
            status=status,
            allowed_actions=current.allowed_actions,
            response_schema=current.response_schema,
            created_at=current.created_at,
            expires_at=current.expires_at,
            session_id=current.session_id,
            run_id=current.run_id,
        )
        return receipt


@pytest.mark.parametrize("kind", tuple(ActionInboxKind))
def test_action_inbox_item_round_trips_each_closed_kind(
    kind: ActionInboxKind,
) -> None:
    item = _item(kind)
    payload = item.to_dict()

    assert payload["wire_kind"] == "gigaloom.action_inbox.item.v1"
    assert payload["kind"] == kind.value
    assert "prompt" not in payload
    assert "form" not in payload
    assert "oauth" not in payload
    assert ActionInboxItem.from_dict(payload) == item


def test_action_inbox_snapshot_is_bounded_cross_run_and_deterministic() -> None:
    owners = tuple(
        _Owner(
            f"owner.{kind.value}",
            (
                _item(
                    kind,
                    created_at=f"2026-07-30T12:00:0{index}Z",
                ),
            ),
        )
        for index, kind in enumerate(ActionInboxKind)
    )
    service = ActionInboxService(tuple(reversed(owners)))

    snapshot = service.snapshot(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        limit=3,
    )
    repeated = ActionInboxService(owners).snapshot(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        limit=3,
    )

    assert len(snapshot.items) == 3
    assert [item.created_at for item in snapshot.items] == sorted(
        (item.created_at for owner in owners for item in owner.items.values()),
        reverse=True,
    )[:3]
    assert repeated.snapshot_sha256 == snapshot.snapshot_sha256
    with pytest.raises(ValueError, match="supported range"):
        service.snapshot(
            owner_id=OWNER_ID,
            workspace_id=WORKSPACE_ID,
            limit=MAX_ACTION_INBOX_ITEMS + 1,
        )


def test_action_inbox_response_is_idempotent_after_owner_resolves_item() -> None:
    item = _item(ActionInboxKind.AUTOMATION_QUESTION)
    owner = _Owner(item.authority, (item,))
    service = ActionInboxService((owner,))
    request = ActionInboxResponseRequest(
        item_id=item.item_id,
        authority=item.authority,
        owner_id=item.owner_id,
        workspace_id=item.workspace_id,
        expected_revision=item.revision,
        expected_item_sha256=item.item_sha256,
        action=ActionInboxCommand.ANSWER,
        idempotency_key="answer-1",
        response={"answer": "proceed"},
    )

    first = service.respond(request)
    repeated = service.respond(request)

    assert first.status is ActionInboxStatus.ANSWERED
    assert repeated.response_id == first.response_id
    assert repeated.receipt_sha256 == first.receipt_sha256
    assert repeated.idempotent_replay is True
    assert owner.respond_calls == 1


@pytest.mark.parametrize(
    ("patch", "error"),
    [
        ({"expected_revision": "stale-revision"}, ActionInboxConflictError),
        ({"expected_item_sha256": "f" * 64}, ActionInboxConflictError),
        ({"owner_id": "other-owner"}, ActionInboxForbiddenError),
        ({"workspace_id": "other-workspace"}, ActionInboxForbiddenError),
    ],
)
def test_action_inbox_response_fails_closed_before_owner_mutation(
    patch: Mapping[str, object],
    error: type[Exception],
) -> None:
    item = _item(ActionInboxKind.APPROVAL)
    owner = _Owner(item.authority, (item,))
    service = ActionInboxService((owner,))
    request = ActionInboxResponseRequest(
        item_id=item.item_id,
        authority=item.authority,
        owner_id=item.owner_id,
        workspace_id=item.workspace_id,
        expected_revision=item.revision,
        expected_item_sha256=item.item_sha256,
        action=ActionInboxCommand.DENY,
        idempotency_key="deny-1",
    )

    with pytest.raises(error):
        service.respond(replace(request, **patch))
    assert owner.respond_calls == 0


@pytest.mark.parametrize(
    "response",
    [
        {"oauth_token": "not-allowed"},
        {"nested": {"client_secret": "not-allowed"}},
        {"answer": "x" * (16 * 1024)},
    ],
)
def test_action_inbox_rejects_secret_or_unbounded_answer_payloads(
    response: Mapping[str, object],
) -> None:
    item = _item(ActionInboxKind.MCP_ELICITATION)
    owner = _Owner(item.authority, (item,))
    request = ActionInboxResponseRequest(
        item_id=item.item_id,
        authority=item.authority,
        owner_id=item.owner_id,
        workspace_id=item.workspace_id,
        expected_revision=item.revision,
        expected_item_sha256=item.item_sha256,
        action=ActionInboxCommand.ANSWER,
        idempotency_key="answer-1",
        response=response,
    )

    with pytest.raises(ActionInboxValidationError):
        ActionInboxService((owner,)).respond(request)
    assert owner.respond_calls == 0


def test_action_inbox_rejects_kind_action_and_item_tampering() -> None:
    with pytest.raises(ValueError, match="invalid action"):
        build_action_inbox_item(
            item_id="login-1",
            kind=ActionInboxKind.PROVIDER_LOGIN,
            authority="providers.login",
            owner_id=OWNER_ID,
            workspace_id=WORKSPACE_ID,
            origin="manual",
            revision="revision-1",
            consequence=ActionConsequence.AUTHENTICATION,
            status=ActionInboxStatus.PENDING,
            allowed_actions=(ActionInboxCommand.ANSWER,),
            response_schema="login.answer.v1",
            created_at=CREATED_AT,
        )

    item = _item(ActionInboxKind.RUN_INPUT)
    tampered = deepcopy(item.to_dict())
    tampered["origin"] = "workflow"
    with pytest.raises(ValueError, match="digest does not match"):
        ActionInboxItem.from_dict(tampered)


def test_runtime_public_api_exposes_action_inbox_without_owner_internals() -> None:
    from gigaloom.runtime import api as runtime_api

    assert runtime_api.ActionInboxService is ActionInboxService
    assert runtime_api.ActionInboxKind is ActionInboxKind
