from __future__ import annotations

from fastapi.testclient import TestClient

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.registry import create_default_registry
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    bundle_to_dict,
)
from gpt2giga_harness.sessions.store import utc_now
from gpt2giga_harness.types import GigaChatApiMode, HarnessCapability
from gpt2giga_harness.ui.app import create_app
from gpt2giga_harness.ui.services.legacy_bundles import (
    LEGACY_FULL_BUNDLE_MARKER,
)


def _app(tmp_path):
    return create_app(
        HarnessConfig(data_dir=str(tmp_path)),
        registry=create_default_registry(include_entry_points=False),
    )


def _run(store, session_id: str):
    return store.create_run(
        session_id=session_id,
        harness_id="echo",
        prompt="legacy compatibility",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="read",
        workspace=None,
    )


def test_legacy_bundle_responses_use_explicit_export_without_body_changes(
    tmp_path,
    monkeypatch,
):
    app = _app(tmp_path)
    store = app.state.harness_session_store
    session = store.create_session(title="legacy bundle")
    run = _run(store, session.id)
    store.append_message(
        HarnessMessage(
            id="msg_legacy_bundle",
            session_id=session.id,
            run_id=run.id,
            role="assistant",
            content="retained compatibility content",
            created_at=utc_now(),
        )
    )
    original_get = store.get_session_bundle
    expected_session = bundle_to_dict(original_get(session.id))
    export_calls: list[str] = []

    def observed_export(session_id: str):
        export_calls.append(session_id)
        return original_get(session_id)

    def forbid_interactive_full_read(*_args, **_kwargs):
        raise AssertionError("legacy route bypassed explicit bundle export")

    monkeypatch.setattr(store, "export_session_bundle", observed_export)
    monkeypatch.setattr(store, "get_session_bundle", forbid_interactive_full_read)

    with TestClient(app) as client:
        session_response = client.get(f"/api/sessions/{session.id}")
        run_response = client.get(f"/api/runs/{run.id}")
        fork_response = client.post(f"/api/runs/{run.id}/fork")

    assert session_response.status_code == 200
    assert session_response.json() == expected_session
    expected_run = {**expected_session, "selected_run_id": run.id}
    assert run_response.status_code == 200
    assert run_response.json() == expected_run
    assert fork_response.status_code == 200
    fork_body = fork_response.json()
    assert fork_body["source_run"]["id"] == run.id
    assert fork_body["session"]["id"] != session.id
    assert fork_body["bundle"]["session"]["id"] == fork_body["session"]["id"]
    assert fork_body["bundle"]["messages"][0]["content"] == (
        "retained compatibility content"
    )
    assert export_calls == [
        session.id,
        session.id,
        fork_body["session"]["id"],
    ]
    assert app.state.harness_async_diagnostics.snapshot()["markers"] == {
        LEGACY_FULL_BUNDLE_MARKER: 3
    }


def test_normal_ui_reads_never_cross_legacy_bundle_boundary(tmp_path, monkeypatch):
    app = _app(tmp_path)
    store = app.state.harness_session_store
    session = store.create_session(title="bounded normal reads")
    run = _run(store, session.id)
    store.append_message(
        HarnessMessage(
            id="msg_bounded_normal",
            session_id=session.id,
            run_id=run.id,
            role="assistant",
            content="bounded normal content",
            created_at=utc_now(),
        )
    )

    def forbid_export(*_args, **_kwargs):
        raise AssertionError("normal UI read crossed the legacy bundle boundary")

    monkeypatch.setattr(store, "export_session_bundle", forbid_export)

    with TestClient(app) as client:
        sessions = client.get("/api/sessions?limit=10")
        preview = client.get(f"/api/sessions/{session.id}/navigation-preview")
        events = client.get(f"/api/sessions/{session.id}/events?run_id={run.id}")
        provenance = client.get(f"/api/runs/{run.id}/provenance")
        content = client.get(
            f"/api/cockpit/sessions/{session.id}/messages/msg_bounded_normal/content"
        )

    assert sessions.status_code == 200
    assert preview.status_code == 200
    assert events.status_code == 200
    assert provenance.status_code == 200
    assert content.status_code == 200
    assert app.state.harness_async_diagnostics.snapshot()["markers"] == {}
