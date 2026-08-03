"""Durable Thread Relay delivery repository contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3

import pytest

from gigaloom.sessions.api import (
    MAX_THREAD_DELIVERY_PAGE_SIZE,
    MAX_THREAD_OUTSTANDING_CHILDREN,
    ThreadAuthorMode,
    ThreadDeliveryCapacityError,
    ThreadDeliveryConflictError,
    ThreadDeliveryIntent,
    ThreadDeliveryRepository,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    ThreadSourceKind,
    ThreadVisibleRole,
)


NOW = datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc)
CONTENT_DIGEST = hashlib.sha256(b"redacted delivery content").hexdigest()


def _locator(thread_id: str) -> ThreadLocatorV1:
    return ThreadLocatorV1(
        source_kind=ThreadSourceKind.GIGALOOM,
        adapter_id="gigaloom-session-v1",
        project_id="project-1",
        thread_id=thread_id,
        actor_scope="actor-1",
        workspace_identity="workspace-1",
        provider_session_ref=None,
        capability_revision="gigaloom-threads-v1",
    )


def _envelope(
    index: int = 1,
    *,
    expires_at: datetime | None = None,
) -> ThreadMessageEnvelopeV1:
    return ThreadMessageEnvelopeV1(
        source_locator=_locator("source-thread"),
        target_locator=_locator("target-thread"),
        actor_binding="actor-1",
        project_binding="project-1",
        role=ThreadVisibleRole.USER,
        author_mode=ThreadAuthorMode.USER_AUTHORED,
        message_ref=f"message-ref-{index}",
        attachment_refs=(),
        intent=ThreadDeliveryIntent.FOLLOW_UP,
        expected_target_revision="target-revision-1",
        expected_active_turn_id=None,
        idempotency_key=f"delivery-key-{index}",
        expires_at=expires_at or NOW + timedelta(minutes=10),
        depth=1,
    )


def _reserve(
    repository: ThreadDeliveryRepository,
    envelope: ThreadMessageEnvelopeV1,
    *,
    now: datetime = NOW,
):
    return repository.reserve(
        envelope,
        content_digest=CONTENT_DIGEST,
        observed_target_revision="target-revision-1",
        now=now,
    )


def test_reservation_is_restart_durable_and_idempotency_bound(tmp_path) -> None:
    repository = ThreadDeliveryRepository(tmp_path)
    envelope = _envelope()

    first = _reserve(repository, envelope)
    restarted = ThreadDeliveryRepository(tmp_path)
    replay = _reserve(restarted, envelope)

    assert first.created is True
    assert replay.created is False
    assert (
        replay.record == first.record == restarted.get(first.record.receipt.delivery_id)
    )
    with pytest.raises(ThreadDeliveryConflictError, match="another delivery"):
        _reserve(restarted, replace(envelope, message_ref="different-message-ref"))


def test_reservation_checks_revision_ttl_and_outstanding_child_bound(tmp_path) -> None:
    repository = ThreadDeliveryRepository(tmp_path)

    with pytest.raises(ThreadDeliveryConflictError, match="revision is stale"):
        repository.reserve(
            _envelope(),
            content_digest=CONTENT_DIGEST,
            observed_target_revision="stale-revision",
            now=NOW,
        )
    with pytest.raises(ThreadDeliveryConflictError, match="expired"):
        _reserve(
            repository,
            _envelope(expires_at=NOW),
        )

    records = tuple(
        _reserve(repository, _envelope(index)).record
        for index in range(MAX_THREAD_OUTSTANDING_CHILDREN)
    )
    with pytest.raises(ThreadDeliveryCapacityError, match="limit reached"):
        _reserve(repository, _envelope(MAX_THREAD_OUTSTANDING_CHILDREN + 1))

    accepted = repository.transition(
        records[0].receipt.delivery_id,
        ThreadDeliveryStatus.ACCEPTED,
        expected_status=ThreadDeliveryStatus.PENDING,
        now=NOW + timedelta(seconds=1),
    )
    repository.transition(
        accepted.receipt.delivery_id,
        ThreadDeliveryStatus.COMPLETED,
        expected_status=ThreadDeliveryStatus.ACCEPTED,
        now=NOW + timedelta(seconds=2),
    )
    assert _reserve(
        repository,
        _envelope(MAX_THREAD_OUTSTANDING_CHILDREN + 1),
    ).created


def test_transitions_are_optimistic_append_only_and_digest_only(tmp_path) -> None:
    repository = ThreadDeliveryRepository(tmp_path)
    pending = _reserve(repository, _envelope()).record

    accepted = repository.transition(
        pending.receipt.delivery_id,
        ThreadDeliveryStatus.ACCEPTED,
        expected_status=ThreadDeliveryStatus.PENDING,
        now=NOW + timedelta(seconds=1),
        run_ref="run-1",
        job_ref="job-1",
        turn_ref="turn-1",
    )
    with pytest.raises(ThreadDeliveryConflictError, match="resnapshot"):
        repository.transition(
            pending.receipt.delivery_id,
            ThreadDeliveryStatus.CANCELLED,
            expected_status=ThreadDeliveryStatus.PENDING,
            now=NOW + timedelta(seconds=2),
            terminal_reason="user_cancelled",
        )
    completed = repository.transition(
        accepted.receipt.delivery_id,
        ThreadDeliveryStatus.COMPLETED,
        expected_status=ThreadDeliveryStatus.ACCEPTED,
        now=NOW + timedelta(seconds=3),
    )

    receipts = repository.receipts(completed.receipt.delivery_id)
    assert [item.status for item in receipts] == [
        ThreadDeliveryStatus.PENDING,
        ThreadDeliveryStatus.ACCEPTED,
        ThreadDeliveryStatus.COMPLETED,
    ]
    assert all(item.content_digest == CONTENT_DIGEST for item in receipts)
    with sqlite3.connect(repository.db_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE thread_delivery_receipts SET status = 'failed'")


def test_restart_recovery_fails_closed_and_expiry_preserves_history(tmp_path) -> None:
    repository = ThreadDeliveryRepository(tmp_path)
    accepted = repository.transition(
        _reserve(repository, _envelope()).record.receipt.delivery_id,
        ThreadDeliveryStatus.ACCEPTED,
        expected_status=ThreadDeliveryStatus.PENDING,
        now=NOW + timedelta(seconds=1),
    )
    pending = _reserve(
        repository,
        _envelope(2, expires_at=NOW + timedelta(seconds=3)),
    ).record

    restarted = ThreadDeliveryRepository(tmp_path)
    recovered = restarted.recover_interrupted(now=NOW + timedelta(seconds=2))
    expired = restarted.expire_due(now=NOW + timedelta(seconds=4))

    assert [item.receipt.delivery_id for item in recovered] == [
        accepted.receipt.delivery_id
    ]
    assert recovered[0].receipt.status is ThreadDeliveryStatus.FAILED
    assert recovered[0].receipt.terminal_reason == "restart_outcome_ambiguous"
    assert [item.receipt.delivery_id for item in expired] == [
        pending.receipt.delivery_id
    ]
    assert expired[0].receipt.status is ThreadDeliveryStatus.EXPIRED
    assert restarted.recover_interrupted(now=NOW + timedelta(seconds=5)) == ()
    assert len(restarted.receipts(accepted.receipt.delivery_id)) == 3


def test_incoming_and_outgoing_queries_are_cursor_bounded(tmp_path) -> None:
    repository = ThreadDeliveryRepository(tmp_path)
    created = tuple(
        _reserve(
            repository,
            _envelope(index),
            now=NOW + timedelta(seconds=index),
        ).record
        for index in range(3)
    )

    first = repository.list_for_thread(
        _locator("source-thread"),
        direction="outgoing",
        limit=2,
    )
    second = repository.list_for_thread(
        _locator("source-thread"),
        direction="outgoing",
        cursor=first.next_cursor,
        limit=2,
    )
    incoming = repository.list_for_thread(
        _locator("target-thread"),
        direction="incoming",
        limit=3,
    )

    assert first.has_more is True and first.next_cursor is not None
    assert second.has_more is False and second.next_cursor is None
    assert {item.receipt.delivery_id for item in (*first.items, *second.items)} == {
        item.receipt.delivery_id for item in created
    }
    assert tuple(item.receipt.delivery_id for item in incoming.items) == tuple(
        item.receipt.delivery_id for item in reversed(created)
    )
    with pytest.raises(ValueError, match="between 1"):
        repository.list_for_thread(
            _locator("target-thread"),
            direction="incoming",
            limit=MAX_THREAD_DELIVERY_PAGE_SIZE + 1,
        )
