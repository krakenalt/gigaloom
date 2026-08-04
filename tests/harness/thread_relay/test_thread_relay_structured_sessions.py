"""GigaLoom-owned structured-session Thread Relay contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from gigaloom.execution.thread_relay import (
    GIGALOOM_THREAD_ADAPTER_ID,
    GIGALOOM_THREAD_CAPABILITY_REVISION,
    GigaLoomStructuredThreadRelay,
    ThreadRelayAuthorizationError,
    ThreadRelayTargetStateError,
)
from gigaloom.runtime.api import RunStatus
from gigaloom.sessions import (
    FilesystemHarnessSessionStore,
    HarnessMessage,
    ThreadAuthorMode,
    ThreadDeliveryIntent,
    ThreadDeliveryRepository,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    ThreadSourceKind,
    ThreadVisibleRole,
)
from gigaloom.sessions.contracts import utc_now
from gigaloom.types import GigaChatApiMode, HarnessCapability


NOW = datetime(2026, 8, 4, 11, 0, tzinfo=timezone.utc)


@dataclass
class _Resolver:
    messages: dict[str, str]

    def resolve(self, message_ref: str) -> str:
        return self.messages[message_ref]


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


class _Steerer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def steer(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        idempotency_key: str,
    ) -> None:
        self.calls.append(
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "content": content,
                "idempotency_key": idempotency_key,
            }
        )


def _session(
    store: FilesystemHarnessSessionStore,
    title: str,
    *,
    actor_scope: str = "actor-1",
    project_id: str = "project-1",
):
    return store.create_session(
        title=title,
        default_harness_id="echo",
        default_model="TestModel",
        metadata={"actor_scope": actor_scope, "project_id": project_id},
    )


def _service(tmp_path, *, with_steer: bool = False):
    store = FilesystemHarnessSessionStore(tmp_path)
    repository = ThreadDeliveryRepository(tmp_path)
    submitter = _Submitter()
    resolver = _Resolver(
        {"message-ref-1": "Please review token=super-secret-value-now"}
    )
    steerer = _Steerer()
    service = GigaLoomStructuredThreadRelay(
        actor_scope="actor-1",
        project_id="project-1",
        session_store=store,
        delivery_repository=repository,
        turn_submitter=submitter,
        message_resolver=resolver,
        steer_port=steerer if with_steer else None,
    )
    return service, store, repository, submitter, resolver, steerer


def _locator(
    session_id: str, *, actor_scope: str = "actor-1", project_id: str = "project-1"
):
    return ThreadLocatorV1(
        source_kind=ThreadSourceKind.GIGALOOM,
        adapter_id=GIGALOOM_THREAD_ADAPTER_ID,
        project_id=project_id,
        thread_id=session_id,
        actor_scope=actor_scope,
        workspace_identity=None,
        provider_session_ref=None,
        capability_revision=GIGALOOM_THREAD_CAPABILITY_REVISION,
    )


def _envelope(
    session_id: str,
    revision: str,
    *,
    index: int = 1,
    intent: ThreadDeliveryIntent = ThreadDeliveryIntent.FOLLOW_UP,
    active_turn_id: str | None = None,
    actor_scope: str = "actor-1",
    project_id: str = "project-1",
    expires_at: datetime | None = None,
) -> ThreadMessageEnvelopeV1:
    return ThreadMessageEnvelopeV1(
        source_locator=_locator(
            "source-thread",
            actor_scope=actor_scope,
            project_id=project_id,
        ),
        target_locator=_locator(
            session_id,
            actor_scope=actor_scope,
            project_id=project_id,
        ),
        actor_binding=actor_scope,
        project_binding=project_id,
        role=ThreadVisibleRole.USER,
        author_mode=ThreadAuthorMode.USER_AUTHORED,
        message_ref="message-ref-1",
        attachment_refs=("attachment-ref-1",),
        intent=intent,
        expected_target_revision=revision,
        expected_active_turn_id=active_turn_id,
        idempotency_key=f"relay-key-{index}",
        expires_at=expires_at or NOW + timedelta(minutes=10),
        depth=0,
    )


def test_list_and_read_are_actor_project_bound_and_message_bounded(tmp_path) -> None:
    service, store, _, _, _, _ = _service(tmp_path)
    target = _session(store, "Target")
    _session(store, "Other actor", actor_scope="actor-2")
    for index, (role, content) in enumerate(
        (
            ("user", "first"),
            ("assistant", "second"),
            ("user", "OPENAI_API_KEY=sk-secret-value-123456"),
        )
    ):
        store.append_message(
            HarnessMessage(
                id=f"message-{index}",
                session_id=target.id,
                run_id=None,
                role=role,
                content=content,
                created_at=(NOW + timedelta(seconds=index)).isoformat(),
            )
        )

    listed = service.list_threads(limit=10)
    first = service.read_thread(_locator(target.id), limit=2)
    earlier = service.read_thread(
        _locator(target.id),
        cursor=first.next_cursor,
        limit=2,
    )

    assert [item.title for item in listed.items] == ["Target"]
    assert listed.items[0].visible_messages == ()
    assert [item.content for item in first.visible_messages] == [
        "second",
        "OPENAI_API_KEY=<redacted>",
    ]
    assert first.next_cursor == "message-1"
    assert [item.content for item in earlier.visible_messages] == ["first"]
    assert "hidden_reasoning_excluded" in first.unsupported_facts
    with pytest.raises(ThreadRelayAuthorizationError, match="not permitted"):
        service.read_thread(_locator(target.id, actor_scope="actor-2"))


def test_follow_up_enqueues_existing_turn_owner_with_content_free_provenance(
    tmp_path,
) -> None:
    service, store, repository, submitter, _, _ = _service(tmp_path)
    target = _session(store, "Target")
    envelope = _envelope(target.id, target.updated_at)

    preview = service.preview(envelope, now=NOW)
    first = service.deliver(envelope, now=NOW)
    replay = service.deliver(envelope, now=NOW + timedelta(seconds=1))

    assert preview.redacted is True
    assert first.record.receipt.status is ThreadDeliveryStatus.COMPLETED
    assert first.record.receipt.job_ref == "job-relay-1"
    assert first.record.receipt.run_ref == "run-relay-1"
    assert first.record.receipt.turn_ref == "message-relay-1"
    assert replay.idempotent_replay is True
    assert replay.record == first.record
    assert len(submitter.calls) == 1
    _, payload, key, origin = submitter.calls[0]
    assert payload["prompt"] == "Please review token=<redacted>"
    assert key == "thread-relay:relay-key-1"
    assert origin == "interactive"
    provenance = payload["extra"]["thread_relay"]
    assert provenance["delivery_id"] == first.record.receipt.delivery_id
    assert provenance["content_digest"] == preview.content_digest
    assert "content" not in provenance
    assert [
        item.status for item in repository.receipts(first.record.receipt.delivery_id)
    ] == [
        ThreadDeliveryStatus.PENDING,
        ThreadDeliveryStatus.ACCEPTED,
        ThreadDeliveryStatus.COMPLETED,
    ]


def test_delivery_rechecks_target_revision_and_scope_before_persistence(
    tmp_path,
) -> None:
    service, store, repository, _, _, _ = _service(tmp_path)
    target = _session(store, "Target")
    stale = _envelope(target.id, target.updated_at)
    store.update_session(target.id, title="Renamed")

    with pytest.raises(ThreadRelayTargetStateError, match="revision changed"):
        service.deliver(stale, now=NOW)
    with pytest.raises(ThreadRelayAuthorizationError, match="scope"):
        service.deliver(
            _envelope(
                target.id,
                store.get_session(target.id).updated_at,
                actor_scope="actor-2",
            ),
            now=NOW,
        )
    assert (
        repository.list_for_thread(
            _locator(target.id), direction="incoming", limit=10
        ).items
        == ()
    )


def test_archived_or_deleted_target_is_denied_before_delivery_persistence(
    tmp_path,
) -> None:
    service, store, repository, submitter, _, _ = _service(tmp_path)
    archived = _session(store, "Archived target")
    archived_envelope = _envelope(archived.id, archived.updated_at)
    store.update_session(archived.id, archived=True)

    with pytest.raises(ThreadRelayTargetStateError, match="archived"):
        service.deliver(archived_envelope, now=NOW)

    deleted = _session(store, "Deleted target")
    deleted_envelope = _envelope(deleted.id, deleted.updated_at, index=2)
    store.delete_session(deleted.id)

    with pytest.raises(ThreadRelayTargetStateError, match="unavailable"):
        service.deliver(deleted_envelope, now=NOW)

    assert submitter.calls == []
    for target in (archived, deleted):
        assert (
            repository.list_for_thread(
                _locator(target.id), direction="incoming", limit=10
            ).items
            == ()
        )


def test_steer_requires_exact_active_turn_and_records_provenance(tmp_path) -> None:
    service, store, repository, _, _, steerer = _service(tmp_path, with_steer=True)
    target = _session(store, "Active target")
    store.create_run(
        session_id=target.id,
        harness_id="echo",
        prompt="active",
        model="TestModel",
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.AGENT_CLI,
        mode="plan",
        workspace=None,
        status=RunStatus.RUNNING,
        started_at=utc_now(),
        metadata={"active_turn_id": "turn-active-1"},
    )
    envelope = _envelope(
        target.id,
        target.updated_at,
        intent=ThreadDeliveryIntent.STEER,
        active_turn_id="turn-active-1",
    )

    outcome = service.deliver(envelope, now=NOW)

    assert steerer.calls == [
        {
            "session_id": target.id,
            "turn_id": "turn-active-1",
            "content": "Please review token=<redacted>",
            "idempotency_key": "relay-key-1",
        }
    ]
    assert outcome.record.receipt.turn_ref == "turn-active-1"
    wrong = _envelope(
        target.id,
        target.updated_at,
        index=2,
        intent=ThreadDeliveryIntent.STEER,
        active_turn_id="turn-stale",
    )
    with pytest.raises(ThreadRelayTargetStateError, match="active turn changed"):
        service.deliver(wrong, now=NOW + timedelta(seconds=1))
    failed = repository.list_for_thread(
        _locator(target.id), direction="incoming", limit=10
    ).items[0]
    assert failed.receipt.status is ThreadDeliveryStatus.FAILED
    assert failed.receipt.terminal_reason == "delivery_target_mutation_failed"


def test_pending_cancellation_and_expiry_are_scope_bound(tmp_path) -> None:
    service, store, repository, _, resolver, _ = _service(tmp_path)
    target = _session(store, "Target")
    envelope = _envelope(target.id, target.updated_at)
    content = resolver.resolve(envelope.message_ref)
    pending = repository.reserve(
        envelope,
        content_digest=service.preview(envelope, now=NOW).content_digest,
        observed_target_revision=target.updated_at,
        now=NOW,
    ).record

    cancelled = service.cancel_pending(pending.receipt.delivery_id, now=NOW)

    assert content
    assert cancelled.receipt.status is ThreadDeliveryStatus.CANCELLED
    expiring = _envelope(
        target.id,
        target.updated_at,
        index=2,
        expires_at=NOW + timedelta(seconds=1),
    )
    repository.reserve(
        expiring,
        content_digest=service.preview(expiring, now=NOW).content_digest,
        observed_target_revision=target.updated_at,
        now=NOW,
    )
    foreign = _envelope(
        target.id,
        target.updated_at,
        index=3,
        actor_scope="actor-2",
        project_id="project-2",
        expires_at=NOW + timedelta(seconds=1),
    )
    foreign_record = repository.reserve(
        foreign,
        content_digest=service.preview(expiring, now=NOW).content_digest,
        observed_target_revision=target.updated_at,
        now=NOW,
    ).record
    expired = service.expire_pending(now=NOW + timedelta(seconds=2))
    assert len(expired) == 1
    assert expired[0].receipt.status is ThreadDeliveryStatus.EXPIRED
    assert (
        repository.get(foreign_record.receipt.delivery_id).receipt.status
        is ThreadDeliveryStatus.PENDING
    )
