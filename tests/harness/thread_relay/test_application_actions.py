"""Production Thread Relay action composition and scope binding."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from gigaloom.execution.thread_relay import (
    GigaLoomThreadRelayActions,
    LOCAL_THREAD_ACTOR_SCOPE,
    ThreadRelayAuthorizationError,
    ThreadRelayScopeV1,
    ThreadRelayUnsupportedError,
)
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
    ThreadDeliveryRepository,
)


NOW = datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc)


class _ApprovalVerifier:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str, str]] = []

    def verify(
        self,
        receipt_ref: str,
        *,
        actor_scope: str,
        project_id: str,
        preview_digest: str,
    ) -> bool:
        self.calls.append((receipt_ref, actor_scope, project_id, preview_digest))
        return self.allowed


class _Submitter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Mapping[str, Any], str, str]] = []

    def submit_turn(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        origin: str = "interactive",
    ) -> object:
        self.calls.append((session_id, payload, idempotency_key, origin))
        return SimpleNamespace(
            job=SimpleNamespace(id="job-relay-1"),
            queued=SimpleNamespace(
                run=SimpleNamespace(id="run-relay-1"),
                user_message=SimpleNamespace(id="message-relay-1"),
            ),
        )


def _actions(
    tmp_path,
    *,
    actor_scope: str = LOCAL_THREAD_ACTOR_SCOPE,
    approval_verifier: _ApprovalVerifier | None = None,
):
    store = FilesystemHarnessSessionStore(tmp_path)
    submitter = _Submitter()
    actions = GigaLoomThreadRelayActions(
        scope=ThreadRelayScopeV1(actor_scope, "project-1"),
        session_store=store,
        delivery_repository=ThreadDeliveryRepository(tmp_path),
        turn_submitter=submitter,
        approval_verifier=approval_verifier,
        clock=lambda: NOW,
    )
    return actions, store, submitter


def _request(thread_id: str, revision: str) -> dict[str, Any]:
    return {
        "source": "gigaloom",
        "source_thread_id": None,
        "thread_id": thread_id,
        "text": "Please review token=super-secret-value-now",
        "intent": "follow_up",
        "author_mode": "user_authored",
        "expected_target_revision": revision,
        "expected_active_turn_id": None,
        "idempotency_key": "delivery-key-1",
        "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        "attachment_refs": [],
    }


def test_actions_list_preview_deliver_and_report_without_content_echo(tmp_path) -> None:
    actions, store, submitter = _actions(tmp_path)
    target = store.create_session(
        title="Target",
        metadata={"catalog_project_id": "project-1"},
    )
    source = store.create_session(
        title="Source",
        metadata={"catalog_project_id": "project-1"},
    )
    request = _request(target.id, target.updated_at)
    request["source_thread_id"] = source.id
    request["attachment_refs"] = ["attachment-ref-1"]

    listed = actions.list_threads(source="gigaloom", cursor=None, limit=10)
    preview = actions.preview_send(request)
    delivered = actions.send(request, preview_digest=preview["preview_digest"])
    receipt = delivered["receipt"]
    status = actions.status(receipt["delivery_id"])
    incoming = actions.list_deliveries(
        source="gigaloom",
        thread_id=target.id,
        direction="incoming",
        cursor=None,
        limit=10,
    )
    outgoing = actions.list_deliveries(
        source="gigaloom",
        thread_id=source.id,
        direction="outgoing",
        cursor=None,
        limit=10,
    )

    assert {item["locator"]["thread_id"] for item in listed["threads"]} == {
        source.id,
        target.id,
    }
    assert len(preview["preview_digest"]) == 64
    assert "super-secret" not in repr(preview)
    assert preview["attachment_omissions"] == [
        {
            "attachment_ref": "attachment-ref-1",
            "reason_id": "attachment_transfer_unavailable",
            "status": "not_transferred",
        }
    ]
    assert receipt["status"] == "completed"
    assert status["receipt"]["delivery_id"] == receipt["delivery_id"]
    assert incoming["items"][0]["receipt"]["delivery_id"] == receipt["delivery_id"]
    assert outgoing["items"][0]["direction"] == "outgoing"
    assert "envelope_digest" in outgoing["items"][0]
    assert "text" not in repr(outgoing)
    assert len(submitter.calls) == 1
    assert submitter.calls[0][1]["prompt"] == "Please review token=<redacted>"
    assert (
        submitter.calls[0][1]["extra"]["thread_relay"]["attachment_omissions"]
        == preview["attachment_omissions"]
    )


def test_legacy_local_actor_fallback_never_admits_remote_actor(tmp_path) -> None:
    local, store, _ = _actions(tmp_path)
    target = store.create_session(
        title="Legacy local",
        metadata={"project_id": "project-1"},
    )
    remote = GigaLoomThreadRelayActions(
        scope=ThreadRelayScopeV1("remote-actor", "project-1"),
        session_store=store,
        delivery_repository=ThreadDeliveryRepository(tmp_path),
        turn_submitter=_Submitter(),
        clock=lambda: NOW,
    )

    assert (
        local.read_thread(
            source="gigaloom", thread_id=target.id, cursor=None, limit=10
        )["thread"]["locator"]["actor_scope"]
        == LOCAL_THREAD_ACTOR_SCOPE
    )
    assert (
        remote.list_threads(source="gigaloom", cursor=None, limit=10)["threads"] == []
    )
    with pytest.raises(ThreadRelayAuthorizationError, match="actor"):
        remote.read_thread(
            source="gigaloom", thread_id=target.id, cursor=None, limit=10
        )


def test_unconfigured_sources_and_agent_authorship_fail_closed(tmp_path) -> None:
    actions, store, _ = _actions(tmp_path)
    target = store.create_session(
        title="Target",
        metadata={"catalog_project_id": "project-1"},
    )

    with pytest.raises(ThreadRelayUnsupportedError, match="live adapter"):
        actions.list_threads(source="codex", cursor=None, limit=10)

    request = _request(target.id, target.updated_at)
    request["author_mode"] = "agent_proposed_user_approved"
    with pytest.raises(ThreadRelayUnsupportedError, match="dedicated approval surface"):
        actions.preview_send(request)


def test_dedicated_agent_approval_entrypoint_preserves_authorship_and_receipt(
    tmp_path,
) -> None:
    verifier = _ApprovalVerifier()
    actions, store, submitter = _actions(tmp_path, approval_verifier=verifier)
    target = store.create_session(
        title="Approved target",
        metadata={"catalog_project_id": "project-1"},
    )
    request = _request(target.id, target.updated_at)
    request["author_mode"] = "agent_proposed_user_approved"

    preview = actions.preview_agent_send(request)
    delivered = actions.send_agent_approved(
        request,
        preview_digest=preview["preview_digest"],
        approval_receipt_ref="approval-1",
    )
    receipt = delivered["receipt"]
    record = actions.delivery_repository.get(receipt["delivery_id"])

    assert record.envelope.author_mode.value == "agent_proposed_user_approved"
    assert receipt["status"] == "completed"
    assert len(submitter.calls) == 1
    assert verifier.calls == [
        (
            "approval-1",
            LOCAL_THREAD_ACTOR_SCOPE,
            "project-1",
            preview["preview_digest"],
        )
    ]


def test_agent_send_cannot_bypass_missing_or_denied_approval_verifier(tmp_path) -> None:
    actions, store, _ = _actions(tmp_path)
    target = store.create_session(
        title="Unapproved target",
        metadata={"catalog_project_id": "project-1"},
    )
    request = _request(target.id, target.updated_at)
    request["author_mode"] = "agent_proposed_user_approved"
    preview = actions.preview_agent_send(request)

    with pytest.raises(ThreadRelayUnsupportedError, match="verifier is unavailable"):
        actions.send_agent_approved(
            request,
            preview_digest=preview["preview_digest"],
            approval_receipt_ref="approval-missing",
        )

    verifier = _ApprovalVerifier(allowed=False)
    denied, denied_store, _ = _actions(
        tmp_path / "denied",
        approval_verifier=verifier,
    )
    denied_target = denied_store.create_session(
        title="Denied target",
        metadata={"catalog_project_id": "project-1"},
    )
    denied_request = _request(denied_target.id, denied_target.updated_at)
    denied_request["author_mode"] = "agent_proposed_user_approved"
    denied_preview = denied.preview_agent_send(denied_request)
    with pytest.raises(ThreadRelayAuthorizationError, match="not valid"):
        denied.send_agent_approved(
            denied_request,
            preview_digest=denied_preview["preview_digest"],
            approval_receipt_ref="approval-denied",
        )
