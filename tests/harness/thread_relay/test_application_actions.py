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


def _actions(tmp_path, *, actor_scope: str = LOCAL_THREAD_ACTOR_SCOPE):
    store = FilesystemHarnessSessionStore(tmp_path)
    submitter = _Submitter()
    actions = GigaLoomThreadRelayActions(
        scope=ThreadRelayScopeV1(actor_scope, "project-1"),
        session_store=store,
        delivery_repository=ThreadDeliveryRepository(tmp_path),
        turn_submitter=submitter,
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
    request = _request(target.id, target.updated_at)

    listed = actions.list_threads(source="gigaloom", cursor=None, limit=10)
    preview = actions.preview_send(request)
    delivered = actions.send(request, preview_digest=preview["preview_digest"])
    receipt = delivered["receipt"]
    status = actions.status(receipt["delivery_id"])

    assert [item["locator"]["thread_id"] for item in listed["threads"]] == [target.id]
    assert len(preview["preview_digest"]) == 64
    assert "super-secret" not in repr(preview)
    assert receipt["status"] == "completed"
    assert status["receipt"]["delivery_id"] == receipt["delivery_id"]
    assert len(submitter.calls) == 1
    assert submitter.calls[0][1]["prompt"] == "Please review token=<redacted>"


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
