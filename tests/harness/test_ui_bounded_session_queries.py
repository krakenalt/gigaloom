from __future__ import annotations

from fastapi.testclient import TestClient

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.registry import create_default_registry
from gpt2giga_harness.sessions import FilesystemHarnessSessionStore
from gpt2giga_harness.sessions.models import HarnessMessage, HarnessStoredEvent
from gpt2giga_harness.sessions.store import utc_now
from gpt2giga_harness.types import GigaChatApiMode, HarnessCapability
from gpt2giga_harness.ui.app import create_app
from gpt2giga_harness.ui.services.session_queries import list_session_window


def _app(tmp_path):
    return create_app(
        HarnessConfig(data_dir=str(tmp_path)),
        registry=create_default_registry(include_entry_points=False),
    )


def _run(store, session_id: str):
    return store.create_run(
        session_id=session_id,
        harness_id="echo",
        prompt="bounded UI",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
    )


def test_first_party_ui_reads_use_bounded_session_contracts(tmp_path, monkeypatch):
    app = _app(tmp_path)
    store = app.state.harness_session_store
    session = store.create_session(title="bounded")
    run = _run(store, session.id)
    message = store.append_message(
        HarnessMessage(
            id="msg_bounded",
            session_id=session.id,
            run_id=run.id,
            role="assistant",
            content="bounded content",
            created_at=utc_now(),
        )
    )
    event = store.append_event(
        HarnessStoredEvent(
            id="evt_bounded",
            session_id=session.id,
            run_id=run.id,
            type="progress",
            message="bounded event",
            payload={"bounded": True},
            created_at=utc_now(),
        )
    )
    store.append_raw_request(
        session_id=session.id,
        run_id=run.id,
        payload={"prompt": "bounded request"},
    )
    store.append_raw_response(
        session_id=session.id,
        run_id=run.id,
        payload={"result": "bounded response"},
    )

    def forbid_full_scan(*_args, **_kwargs):
        raise AssertionError("interactive UI read used a legacy full-history scan")

    with TestClient(app) as client:
        for method in (
            "list_sessions",
            "list_messages",
            "list_runs",
            "list_events",
            "list_raw_requests",
            "list_raw_responses",
        ):
            monkeypatch.setattr(store, method, forbid_full_scan)

        sessions = client.get("/api/sessions?limit=10")
        preview = client.get(f"/api/sessions/{session.id}/navigation-preview")
        events = client.get(f"/api/sessions/{session.id}/events?run_id={run.id}")
        content = client.get(
            f"/api/cockpit/sessions/{session.id}/messages/{message.id}/content"
        )
        raw = client.get(f"/api/cockpit/runs/{run.id}/raw")
        provenance = client.get(f"/api/runs/{run.id}/provenance")
        event_payload = client.get(f"/api/runs/{run.id}/events/{event.id}")

    assert sessions.status_code == 200
    assert [item["id"] for item in sessions.json()["sessions"]] == [session.id]
    assert preview.status_code == 200
    assert preview.json()["transcript"] == [
        f"ASSISTANT · {message.created_at}\nbounded content"
    ]
    assert [item["id"] for item in events.json()["events"]] == [event.id]
    assert content.json()["content"] == "bounded content"
    assert {item["direction"] for item in raw.json()["records"]} == {
        "request",
        "response",
    }
    assert provenance.status_code == 200
    assert provenance.json()["provenance"]["run_id"] == run.id
    assert event_payload.json()["payload"] == {"bounded": True}


def test_large_tui_windows_return_latest_bounded_records(tmp_path):
    app = _app(tmp_path)
    store = app.state.harness_session_store
    session = store.create_session(title="large")
    run = _run(store, session.id)
    for index in range(120):
        store.append_message(
            HarnessMessage(
                id=f"msg_{index:03d}",
                session_id=session.id,
                run_id=run.id,
                role="assistant",
                content=f"message {index}",
                created_at=utc_now(),
            )
        )
        store.append_event(
            HarnessStoredEvent(
                id=f"evt_{index:03d}",
                session_id=session.id,
                run_id=run.id,
                type="progress",
                message=f"event {index}",
                payload={"index": index},
                created_at=utc_now(),
            )
        )

    with TestClient(app) as client:
        preview = client.get(f"/api/sessions/{session.id}/navigation-preview")
        events = client.get(f"/api/sessions/{session.id}/events?run_id={run.id}")

    preview_body = preview.json()
    assert preview.status_code == 200
    assert preview_body["match_count"] == 120
    assert len(preview_body["transcript"]) == 100
    assert preview_body["transcript"][0].endswith("message 20")
    assert preview_body["transcript"][-1].endswith("message 119")
    assert preview_body["truncated"] is True

    event_items = events.json()["events"]
    assert events.status_code == 200
    assert len(event_items) == 100
    assert event_items[0]["id"] == "evt_020"
    assert event_items[-1]["id"] == "evt_119"


def test_direct_message_lookup_rejects_cross_session_identity(tmp_path):
    app = _app(tmp_path)
    store = app.state.harness_session_store
    owner = store.create_session(title="owner")
    other = store.create_session(title="other")
    run = _run(store, owner.id)
    store.append_message(
        HarnessMessage(
            id="msg_owner",
            session_id=owner.id,
            run_id=run.id,
            role="assistant",
            content="private to owner path",
            created_at=utc_now(),
        )
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/cockpit/sessions/{other.id}/messages/msg_owner/content"
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Message not found"


def test_session_window_continues_after_an_excluded_index_page(tmp_path):
    store = FilesystemHarnessSessionStore(tmp_path)
    for index in range(205):
        store.create_session(title=f"session {index}")
    first_page = store.list_sessions_page(
        project_id=None,
        workspace=None,
        harness_id=None,
        q=None,
        include_archived=False,
        cursor=None,
        limit=200,
    )
    excluded = {session.id for session in first_page.items}

    selected = list_session_window(
        store,
        project_id=None,
        workspace=None,
        harness_id=None,
        q=None,
        include_archived=False,
        limit=5,
        excluded_ids=excluded,
    )

    assert len(selected) == 5
    assert not excluded.intersection(session.id for session in selected)
