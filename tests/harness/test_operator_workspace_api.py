"""Bounded Operator Evidence, Action Inbox, and event replay APIs."""

from __future__ import annotations

from dataclasses import replace
import hashlib

from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.review.workspace.api import (
    EvidenceOmission,
    EvidenceOmissionReason,
    EvidenceSection,
    EvidenceWorkspaceProjection,
    EvidenceWorkspaceRun,
    build_evidence_workspace,
)
from gigaloom.runtime.action_inbox.api import (
    ActionConsequence,
    ActionInboxCommand,
    ActionInboxConflictError,
    ActionInboxItem,
    ActionInboxKind,
    ActionInboxOwnerPort,
    ActionInboxResponseRequest,
    ActionInboxResponseResult,
    ActionInboxService,
    ActionInboxStatus,
    build_action_inbox_item,
)
from gigaloom.ui.app import create_app
from gigaloom.ui.streaming.operator_events import (
    OperatorEventBroker,
    operator_event_sse,
    operator_resnapshot_sse,
)


OWNER_ID = "local_operator"
WORKSPACE_ID = "workspace-1"
AUTHORITY = "runtime.questions"


class _EvidenceOwner:
    def __init__(self, projection: EvidenceWorkspaceProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[str, str, str]] = []

    def get_evidence_workspace(
        self,
        *,
        run_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> EvidenceWorkspaceProjection:
        self.calls.append((run_id, owner_id, workspace_id))
        return self.projection


class _InboxOwner(ActionInboxOwnerPort):
    authority = AUTHORITY

    def __init__(self, items: tuple[ActionInboxItem, ...]) -> None:
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
        return tuple(
            item
            for item in self.items.values()
            if item.status is ActionInboxStatus.PENDING
        )[:limit]

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
        receipt = ActionInboxResponseResult(
            response_id=f"response-{request.item_id}",
            item_id=request.item_id,
            authority=request.authority,
            owner_id=request.owner_id,
            workspace_id=request.workspace_id,
            action=request.action,
            status=ActionInboxStatus.ANSWERED,
            revision="response-revision-1",
            receipt_sha256=hashlib.sha256(
                f"{request.item_id}:{request.idempotency_key}".encode()
            ).hexdigest(),
        )
        self.responses[request.idempotency_key] = (request, receipt)
        self.items[request.item_id] = replace(
            self.items[request.item_id],
            status=ActionInboxStatus.ANSWERED,
            revision="item-revision-2",
        )
        return receipt


def _projection(
    *,
    owner_id: str = OWNER_ID,
    workspace_id: str = WORKSPACE_ID,
) -> EvidenceWorkspaceProjection:
    return build_evidence_workspace(
        run=EvidenceWorkspaceRun(
            run_id="run-1",
            session_id="session-1",
            owner_id=owner_id,
            workspace_id=workspace_id,
            status="succeeded",
            revision="run-revision-1",
        ),
        omissions=tuple(
            EvidenceOmission(
                section=section,
                reason=EvidenceOmissionReason.NOT_RECORDED,
                authority=f"review.{section.value}",
            )
            for section in EvidenceSection
        ),
    )


def _item(
    index: int,
    *,
    owner_id: str = OWNER_ID,
    workspace_id: str = WORKSPACE_ID,
    kind: ActionInboxKind = ActionInboxKind.AUTOMATION_QUESTION,
    origin: str = "automation",
) -> ActionInboxItem:
    return build_action_inbox_item(
        item_id=f"question-{index}",
        kind=kind,
        authority=AUTHORITY,
        owner_id=owner_id,
        workspace_id=workspace_id,
        origin=origin,
        revision=f"item-revision-{index}",
        consequence=ActionConsequence.RUN_CONTROL,
        status=ActionInboxStatus.PENDING,
        allowed_actions=(
            ActionInboxCommand.ANSWER,
            ActionInboxCommand.CANCEL,
        ),
        response_schema="automation.answer.v1",
        created_at=f"2026-07-30T12:00:{index:02d}Z",
        session_id="session-1",
        run_id=f"run-{index}",
    )


def _client(
    tmp_path,
    *,
    evidence: _EvidenceOwner | None = None,
    owner: _InboxOwner | None = None,
    broker: OperatorEventBroker | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            HarnessConfig(data_dir=str(tmp_path / "data")),
            operator_evidence_query=evidence,
            action_inbox_service=ActionInboxService((owner,)) if owner else None,
            operator_event_broker=broker,
        )
    )


def _response_payload(item: ActionInboxItem) -> dict[str, object]:
    return {
        "workspace_id": item.workspace_id,
        "expected_revision": item.revision,
        "expected_item_sha256": item.item_sha256,
        "action": "answer",
        "idempotency_key": "answer-1",
        "response": {"answer": "continue"},
    }


def test_evidence_endpoint_verifies_owner_and_workspace_binding(tmp_path) -> None:
    evidence = _EvidenceOwner(_projection())
    client = _client(tmp_path, evidence=evidence)

    response = client.get(
        "/api/operator/runs/run-1/evidence",
        params={"workspace_id": WORKSPACE_ID},
    )

    assert response.status_code == 200
    assert response.json()["evidence"] == evidence.projection.to_dict()
    assert evidence.calls == [("run-1", OWNER_ID, WORKSPACE_ID)]

    evidence.projection = _projection(owner_id="other-owner")
    response = client.get(
        "/api/operator/runs/run-1/evidence",
        params={"workspace_id": WORKSPACE_ID},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "evidence_binding_mismatch"


def test_evidence_endpoint_reports_unavailable_owner_without_fallback(tmp_path) -> None:
    response = _client(tmp_path).get(
        "/api/operator/runs/run-1/evidence",
        params={"workspace_id": WORKSPACE_ID},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "evidence_unavailable"


def test_inbox_pages_are_bounded_filter_and_snapshot_bound(tmp_path) -> None:
    owner = _InboxOwner(
        (
            _item(1),
            _item(2, kind=ActionInboxKind.RUN_INPUT, origin="run"),
            _item(3),
            _item(4),
            _item(5),
        )
    )
    client = _client(tmp_path, owner=owner)

    first = client.get(
        "/api/operator/inbox",
        params={"workspace_id": WORKSPACE_ID, "limit": 2},
    )
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert first.json()["has_more"] is True

    second = client.get(
        "/api/operator/inbox",
        params={
            "workspace_id": WORKSPACE_ID,
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 2

    changed_filter = client.get(
        "/api/operator/inbox",
        params={
            "workspace_id": WORKSPACE_ID,
            "limit": 2,
            "kind": "run_input",
            "cursor": first.json()["next_cursor"],
        },
    )
    assert changed_filter.status_code == 409
    assert changed_filter.json()["detail"]["code"] == "resnapshot_required"

    filtered = client.get(
        "/api/operator/inbox",
        params={
            "workspace_id": WORKSPACE_ID,
            "kind": "run_input",
            "origin": "run",
        },
    )
    assert [item["item_id"] for item in filtered.json()["items"]] == ["question-2"]

    owner.items["question-6"] = _item(6)
    stale = client.get(
        "/api/operator/inbox",
        params={
            "workspace_id": WORKSPACE_ID,
            "limit": 2,
            "cursor": first.json()["next_cursor"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "resnapshot_required"


def test_inbox_response_is_conflict_safe_idempotent_and_evented_once(
    tmp_path,
) -> None:
    item = _item(1)
    owner = _InboxOwner((item,))
    broker = OperatorEventBroker()
    cursor = broker.cursor(owner_id=OWNER_ID, workspace_id=WORKSPACE_ID)
    client = _client(tmp_path, owner=owner, broker=broker)
    path = f"/api/operator/inbox/{AUTHORITY}/{item.item_id}/responses"

    first = client.post(path, json=_response_payload(item))
    repeated = client.post(path, json=_response_payload(item))

    assert first.status_code == 200
    assert first.json()["result"]["idempotent_replay"] is False
    assert repeated.status_code == 200
    assert repeated.json()["result"]["idempotent_replay"] is True
    assert owner.respond_calls == 1
    events = broker.read(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        after=cursor,
    )
    assert [event.kind for event in events.events] == ["inbox.changed"]


def test_inbox_response_rejects_stale_cross_scope_and_secret_payloads(
    tmp_path,
) -> None:
    item = _item(1)
    owner = _InboxOwner((item,))
    client = _client(tmp_path, owner=owner)
    path = f"/api/operator/inbox/{AUTHORITY}/{item.item_id}/responses"

    stale_payload = _response_payload(item)
    stale_payload["expected_revision"] = "stale-revision"
    stale = client.post(path, json=stale_payload)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_action"

    cross_scope_payload = _response_payload(item)
    cross_scope_payload["workspace_id"] = "other-workspace"
    forbidden = client.post(path, json=cross_scope_payload)
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "action_forbidden"

    secret_payload = _response_payload(item)
    secret_payload["response"] = {"oauth_token": "do-not-echo"}
    rejected = client.post(path, json=secret_payload)
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "invalid_action"
    assert "do-not-echo" not in rejected.text
    assert owner.respond_calls == 0


def test_event_replay_is_bounded_content_free_and_resnapshot_explicit() -> None:
    broker = OperatorEventBroker(max_events_per_scope=2)
    initial = broker.cursor(owner_id=OWNER_ID, workspace_id=WORKSPACE_ID)
    events = [
        broker.publish(
            owner_id=OWNER_ID,
            workspace_id=WORKSPACE_ID,
            kind="inbox.changed",
            resource_id=f"question-{index}",
            revision=f"revision-{index}",
            sha256=str(index) * 64,
        )
        for index in range(1, 4)
    ]

    slow = broker.read(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        after=initial,
    )
    assert slow.events == ()
    assert slow.resnapshot_reason == "slow_consumer"

    changed_generation = broker.read(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        after="op1.0000000000000000.0",
    )
    assert changed_generation.resnapshot_reason == "generation_changed"

    replay = broker.read(
        owner_id=OWNER_ID,
        workspace_id=WORKSPACE_ID,
        after=events[1].cursor,
    )
    assert replay.events == (events[2],)
    assert "event: update" in operator_event_sse(events[2])
    assert "event: resnapshot" in operator_resnapshot_sse(slow)
    serialized = operator_event_sse(events[2])
    assert "socket" not in serialized
    assert "content" not in serialized
