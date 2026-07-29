import asyncio
from dataclasses import replace

import pytest

import gpt2giga_harness.sessions.storage.filesystem.events as event_storage
from gpt2giga_harness.sessions import (
    EventPersistenceClass,
    FilesystemHarnessSessionStore,
    InMemoryHarnessSessionStore,
    classify_event_persistence,
)
from gpt2giga_harness.sessions.event_persistence import (
    MAX_EVENT_APPEND_BATCH_RECORDS,
)
from gpt2giga_harness.sessions.event_stream import RunEventBroker, StreamSignal
from gpt2giga_harness.sessions.models import HarnessStoredEvent
from gpt2giga_harness.sessions.store import utc_now


def _event(event_id: str, *, run_id: str = "run-one") -> HarnessStoredEvent:
    return HarnessStoredEvent(
        id=event_id,
        session_id="session-one",
        run_id=run_id,
        type="message_delta",
        message=event_id,
        payload={"delta": event_id},
        created_at=utc_now(),
    )


def test_event_persistence_classification_is_conservative():
    assert (
        classify_event_persistence("message_delta")
        is EventPersistenceClass.PRESENTATION_DELTA
    )
    assert (
        classify_event_persistence("run_finished") is EventPersistenceClass.FINAL_STATE
    )
    assert classify_event_persistence("error") is EventPersistenceClass.FINAL_STATE
    assert (
        classify_event_persistence("warning") is EventPersistenceClass.CRITICAL_CONTROL
    )
    assert (
        classify_event_persistence("future_unknown_event")
        is EventPersistenceClass.CRITICAL_CONTROL
    )


def test_active_event_appender_batches_deltas_and_forces_durable_boundaries(
    tmp_path,
    monkeypatch,
):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="active")
    locate_calls = 0
    manifest_reads = 0
    fsync_calls = 0
    original_locate = store._session_locator.locate
    original_read_json = event_storage._read_json
    original_fsync = event_storage.os.fsync

    def counted_locate(session_id):
        nonlocal locate_calls
        locate_calls += 1
        return original_locate(session_id)

    def counted_read_json(path):
        nonlocal manifest_reads
        if path.name == event_storage.MANIFEST_FILE:
            manifest_reads += 1
        return original_read_json(path)

    def counted_fsync(descriptor):
        nonlocal fsync_calls
        fsync_calls += 1
        return original_fsync(descriptor)

    monkeypatch.setattr(store._session_locator, "locate", counted_locate)
    monkeypatch.setattr(event_storage, "_read_json", counted_read_json)
    monkeypatch.setattr(event_storage.os, "fsync", counted_fsync)

    appender = store.event_appender(session.id)
    records = (
        replace(_event("evt-delta-1"), session_id=session.id),
        replace(_event("evt-delta-2"), session_id=session.id),
        replace(
            _event("evt-control"),
            session_id=session.id,
            type="warning",
        ),
        replace(_event("evt-delta-3"), session_id=session.id),
        replace(
            _event("evt-terminal"),
            session_id=session.id,
            type="run_finished",
        ),
        replace(_event("evt-delta-4"), session_id=session.id),
    )

    assert appender.append_many(records) == records
    assert fsync_calls == 3
    assert locate_calls == 1
    assert manifest_reads == 1

    final = replace(_event("evt-final-delta"), session_id=session.id)
    assert appender.append(final) == final
    assert fsync_calls == 4
    assert locate_calls == 1
    assert manifest_reads == 1

    assert [
        item.event.id
        for item in store.list_event_tail_page(
            session.id,
            run_id=None,
            limit=100,
        ).items
    ] == [event.id for event in (*records, final)]


@pytest.mark.parametrize("store_kind", ["memory", "filesystem"])
def test_event_appender_is_session_bound_and_bounded(tmp_path, store_kind):
    store = (
        InMemoryHarnessSessionStore()
        if store_kind == "memory"
        else FilesystemHarnessSessionStore(tmp_path)
    )
    session = store.create_session(title="bounded")
    appender = store.event_appender(session.id)

    with pytest.raises(ValueError, match="another session"):
        appender.append(_event("evt-wrong-session"))
    with pytest.raises(ValueError, match="at most 64"):
        appender.append_many(
            replace(
                _event(f"evt-{index}"),
                session_id=session.id,
            )
            for index in range(MAX_EVENT_APPEND_BATCH_RECORDS + 1)
        )

    assert store.list_events(session.id) == ()


async def test_run_event_broker_wakes_only_exact_run_and_resnapshots_overflow():
    broker = RunEventBroker(queue_size=2)
    first = broker.subscribe("run-one")
    second = broker.subscribe("run-two")
    try:
        broker.publish(_event("evt-1"))
        broker.publish(_event("evt-2"))
        broker.publish(_event("evt-3"))
        await asyncio.sleep(0)

        assert await first.wait(0.1) is StreamSignal.RESNAPSHOT_REQUIRED
        assert await second.wait(0.01) is None
        assert broker.subscriber_count("run-one") == 1
        assert broker.subscriber_count() == 2
    finally:
        first.close()
        second.close()

    assert broker.subscriber_count() == 0


async def test_session_event_broker_wakes_only_for_session_revisions():
    broker = RunEventBroker(queue_size=2)
    subscription = broker.subscribe_session("session-one")
    try:
        broker.publish(_event("evt-run"))
        await asyncio.sleep(0)
        assert await subscription.wait(0.01) is None

        revision = HarnessStoredEvent(
            **{
                **_event("evt-title").__dict__,
                "type": "session.updated",
            }
        )
        broker.publish(revision)
        await asyncio.sleep(0)

        assert await subscription.wait(0.1) is StreamSignal.CHANGED
        assert broker.session_subscriber_count("session-one") == 1
        assert broker.subscriber_count() == 1
    finally:
        subscription.close()

    assert broker.session_subscriber_count() == 0


async def test_runs_center_broker_is_global_bounded_and_content_free():
    broker = RunEventBroker(queue_size=1)
    subscription = broker.subscribe_runs_center()
    try:
        broker.publish_runs_center()
        broker.publish_runs_center()
        await asyncio.sleep(0)

        assert await subscription.wait(0.1) is StreamSignal.RESNAPSHOT_REQUIRED
        assert broker.runs_center_subscriber_count() == 1
        assert broker.snapshot()["runs_center_subscribers"] == 1
    finally:
        subscription.close()

    assert broker.runs_center_subscriber_count() == 0


@pytest.mark.parametrize("store_kind", ["memory", "filesystem"])
def test_event_tail_pages_are_offset_bounded_and_run_filtered(tmp_path, store_kind):
    store = (
        InMemoryHarnessSessionStore()
        if store_kind == "memory"
        else FilesystemHarnessSessionStore(tmp_path)
    )
    session = store.create_session(title="Tail")
    first = _event("evt-1")
    other = _event("evt-other", run_id="run-two")
    second = _event("evt-2")
    for event in (first, other, second):
        store.append_event(
            HarnessStoredEvent(
                **{
                    **event.__dict__,
                    "session_id": session.id,
                }
            )
        )

    assert store.event_tail_offset(session.id) > 0

    page = store.list_event_tail_page(
        session.id,
        run_id="run-one",
        limit=1,
        max_bytes=1024,
    )
    continued = store.list_event_tail_page(
        session.id,
        run_id="run-one",
        offset=page.next_offset,
        limit=100,
        max_bytes=1024,
    )

    assert [item.event.id for item in page.items] == ["evt-1"]
    assert page.has_more is True
    assert [item.event.id for item in continued.items] == ["evt-2"]
    assert continued.has_more is False
    resolved = store.resolve_event_cursor(
        session.id,
        run_id="run-one",
        event_id="evt-1",
    )
    assert resolved is not None
    assert resolved.offset == page.items[0].next_offset

    session_page = store.list_event_tail_page(
        session.id,
        run_id=None,
        limit=100,
        max_bytes=4096,
    )
    assert [item.event.id for item in session_page.items] == [
        "evt-1",
        "evt-other",
        "evt-2",
    ]


@pytest.mark.parametrize("store_kind", ["memory", "filesystem"])
def test_session_title_compare_and_set_preserves_user_rename(tmp_path, store_kind):
    store = (
        InMemoryHarnessSessionStore()
        if store_kind == "memory"
        else FilesystemHarnessSessionStore(tmp_path)
    )
    session = store.create_session()

    updated = store.update_session_if_title(
        session.id,
        "Untitled session",
        title="Generated title",
    )
    stale = store.update_session_if_title(
        session.id,
        "Untitled session",
        title="Late generated title",
    )

    assert updated is not None
    assert updated.title == "Generated title"
    assert stale is None
    assert store.get_session(session.id).title == "Generated title"
