from __future__ import annotations

from collections.abc import Iterator

import pytest

import gpt2giga_harness.sessions.filesystem as filesystem_sessions
from gpt2giga_harness.sessions import (
    EventNotFoundError,
    FilesystemHarnessSessionStore,
    InMemoryHarnessSessionStore,
    MessageNotFoundError,
    StaleReadSnapshotError,
)
from gpt2giga_harness.sessions.models import HarnessMessage, HarnessStoredEvent
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.types import GigaChatApiMode, HarnessCapability


@pytest.fixture(params=("memory", "filesystem"))
def session_store(request, tmp_path):
    if request.param == "memory":
        return InMemoryHarnessSessionStore()
    return FilesystemHarnessSessionStore(tmp_path)


def _append_message(
    store,
    session_id: str,
    content: str,
    *,
    edited_from: str | None = None,
) -> HarnessMessage:
    metadata = (
        {"edited_from_message_id": edited_from} if edited_from is not None else {}
    )
    return store.append_message(
        HarnessMessage(
            id=new_id("msg"),
            session_id=session_id,
            run_id=None,
            role="user",
            content=content,
            created_at=utc_now(),
            metadata=metadata,
        )
    )


def _create_run(store, session_id: str, prompt: str):
    return store.create_run(
        session_id=session_id,
        harness_id="echo",
        prompt=prompt,
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
    )


def _append_event(store, session_id: str, run_id: str, message: str):
    return store.append_event(
        HarnessStoredEvent(
            id=new_id("evt"),
            session_id=session_id,
            run_id=run_id,
            type="progress",
            message=message,
            payload={"message": message},
            created_at=utc_now(),
        )
    )


def test_recent_messages_preserve_active_edit_branch_and_retained_lookup(
    session_store,
):
    session = session_store.create_session(title="edited")
    first = _append_message(session_store, session.id, "first")
    superseded = _append_message(session_store, session.id, "superseded")
    discarded_tail = _append_message(session_store, session.id, "discarded tail")
    replacement = _append_message(
        session_store,
        session.id,
        "replacement",
        edited_from=superseded.id,
    )

    assert session_store.list_recent_messages(session.id, limit=10) == (
        first,
        replacement,
    )
    assert session_store.get_message(superseded.id) == superseded
    assert session_store.get_message(discarded_tail.id) == discarded_tail
    assert session_store.export_session_bundle(session.id).messages == (
        first,
        superseded,
        discarded_tail,
        replacement,
    )


def test_recent_message_bounds_are_chronological_and_explicit(session_store):
    session = session_store.create_session(title="window")
    messages = tuple(
        _append_message(session_store, session.id, f"message-{index}")
        for index in range(5)
    )

    assert session_store.list_recent_messages(session.id, limit=2) == messages[-2:]
    assert (
        session_store.list_recent_messages(session.id, limit=2, before=messages[3].id)
        == messages[1:3]
    )
    assert (
        session_store.list_recent_messages(session.id, limit=2, through=messages[3].id)
        == messages[2:4]
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        session_store.list_recent_messages(
            session.id,
            limit=2,
            before=messages[2].id,
            through=messages[3].id,
        )
    with pytest.raises(ValueError, match="between 1 and"):
        session_store.list_recent_messages(session.id, limit=0)
    with pytest.raises(MessageNotFoundError):
        session_store.list_recent_messages(session.id, limit=2, before="msg_missing")
    with pytest.raises(MessageNotFoundError):
        session_store.get_message("msg_missing")


def test_run_pages_are_bounded_newest_first_and_snapshot_stable(session_store):
    session = session_store.create_session(title="runs")
    runs = tuple(
        _create_run(session_store, session.id, f"run-{index}") for index in range(5)
    )

    first_page = session_store.list_runs_page(session.id, limit=2)
    assert first_page.items == (runs[4], runs[3])
    assert first_page.has_more is True
    assert first_page.next_cursor is not None

    second_page = session_store.list_runs_page(
        session.id,
        cursor=first_page.next_cursor,
        limit=2,
    )
    assert second_page.items == (runs[2], runs[1])
    assert second_page.has_more is True

    _create_run(session_store, session.id, "new generation")
    with pytest.raises(StaleReadSnapshotError):
        session_store.list_runs_page(
            session.id,
            cursor=second_page.next_cursor,
            limit=2,
        )
    with pytest.raises(ValueError, match="between 1 and"):
        session_store.list_runs_page(session.id, limit=101)


def test_event_queries_are_bounded_and_support_direct_lookup(session_store):
    session = session_store.create_session(title="events")
    run = _create_run(session_store, session.id, "events")
    other = _create_run(session_store, session.id, "other")
    events = (
        _append_event(session_store, session.id, run.id, "one"),
        _append_event(session_store, session.id, other.id, "skip"),
        _append_event(session_store, session.id, run.id, "two"),
    )

    first_page = session_store.list_events_page(
        session.id,
        run_id=run.id,
        limit=1,
    )
    assert tuple(item.event for item in first_page.items) == (events[0],)
    second_page = session_store.list_events_page(
        session.id,
        run_id=run.id,
        offset=first_page.next_offset,
        limit=1,
    )
    assert tuple(item.event for item in second_page.items) == (events[2],)
    assert session_store.get_event(events[1].id) == events[1]
    with pytest.raises(EventNotFoundError):
        session_store.get_event("evt_missing")
    with pytest.raises(ValueError, match="between 1 and"):
        session_store.list_events_page(session.id, limit=0)


def test_raw_queries_are_run_scoped_bounded_and_chronological(session_store):
    session = session_store.create_session(title="raw")
    run = _create_run(session_store, session.id, "target")
    other = _create_run(session_store, session.id, "other")
    requests = tuple(
        session_store.append_raw_request(
            session_id=session.id,
            run_id=run.id,
            payload={"index": index},
        )
        for index in range(3)
    )
    session_store.append_raw_request(
        session_id=session.id,
        run_id=other.id,
        payload={"index": "other"},
    )
    responses = tuple(
        session_store.append_raw_response(
            session_id=session.id,
            run_id=run.id,
            payload={"index": index},
        )
        for index in range(3)
    )

    assert session_store.list_raw_requests_for_run(run.id, limit=2) == requests[-2:]
    assert session_store.list_raw_responses_for_run(run.id, limit=2) == responses[-2:]
    with pytest.raises(ValueError, match="between 1 and"):
        session_store.list_raw_requests_for_run(run.id, limit=0)


def test_filesystem_warm_record_queries_do_not_scan_authoritative_jsonl(
    tmp_path,
    monkeypatch,
):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="warm")
    message = _append_message(store, session.id, "message")
    run = _create_run(store, session.id, "run")
    event = _append_event(store, session.id, run.id, "event")
    request = store.append_raw_request(
        session_id=session.id,
        run_id=run.id,
        payload={"value": "request"},
    )

    reopened = FilesystemHarnessSessionStore(tmp_path)
    assert reopened.list_recent_messages(session.id, limit=1) == (message,)

    def fail_scan(*_args, **_kwargs) -> Iterator[object]:
        raise AssertionError("warm bounded query scanned authoritative JSONL")

    monkeypatch.setattr(
        filesystem_sessions,
        "read_jsonl_with_offsets",
        fail_scan,
    )
    next_message = _append_message(reopened, session.id, "next message")
    next_run = _create_run(reopened, session.id, "next run")
    next_event = _append_event(reopened, session.id, next_run.id, "next event")
    next_request = reopened.append_raw_request(
        session_id=session.id,
        run_id=next_run.id,
        payload={"value": "next request"},
    )

    assert reopened.get_message(message.id) == message
    assert reopened.list_recent_messages(session.id, limit=1) == (next_message,)
    assert reopened.get_event(event.id) == event
    assert reopened.get_event(next_event.id) == next_event
    assert reopened.list_runs_page(session.id, limit=1).items == (next_run,)
    assert reopened.list_raw_requests_for_run(run.id, limit=1) == (request,)
    assert reopened.list_raw_requests_for_run(next_run.id, limit=1) == (next_request,)


def test_filesystem_incomplete_record_projection_rebuilds_from_jsonl(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    session = store.create_session(title="recover")
    message = _append_message(store, session.id, "recover me")
    assert store.list_recent_messages(session.id, limit=1) == (message,)
    store._session_read_index().mark_records_complete(False)

    reopened = FilesystemHarnessSessionStore(tmp_path)

    assert reopened.get_message(message.id) == message
    assert reopened._session_read_index().records_complete() is True
