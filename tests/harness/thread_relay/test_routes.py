from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from gigaloom.cli_commands.commands import thread_relay as thread_commands
from gigaloom.cli_commands.handlers.thread_relay import ThreadRelayCommandHandlers
from gigaloom.config import HarnessConfig
from gigaloom.execution.thread_relay import LOCAL_THREAD_ACTOR_SCOPE
from gigaloom.ui.routers.thread_relay import create_router


PREVIEW_DIGEST = "a" * 64


class _Actions:
    def __init__(self, *, unsafe_preview: bool = False) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.unsafe_preview = unsafe_preview

    def list_threads(self, *, source: str, cursor: str | None, limit: int):
        self.calls.append(("list", (source, cursor, limit)))
        return {"threads": [{"thread_id": "thread-1"}], "next_cursor": None}

    def read_thread(
        self,
        *,
        source: str,
        thread_id: str,
        cursor: str | None,
        limit: int,
    ):
        self.calls.append(("read", (source, thread_id, cursor, limit)))
        return {
            "thread": {
                "thread_id": thread_id,
                "updated_at": "2026-08-04T12:00:00+00:00",
                "messages": [],
            }
        }

    def list_deliveries(
        self,
        *,
        source: str,
        thread_id: str,
        direction: str,
        cursor: str | None,
        limit: int,
    ):
        self.calls.append(("deliveries", (source, thread_id, direction, cursor, limit)))
        return {
            "direction": direction,
            "items": [],
            "next_cursor": None,
            "has_more": False,
        }

    def preview_send(self, payload: Mapping[str, Any]):
        self.calls.append(("preview", dict(payload)))
        result = {
            "preview_digest": PREVIEW_DIGEST,
            "content_digest": "b" * 64,
            "target_revision": payload["expected_target_revision"],
            "intent": payload["intent"],
        }
        if self.unsafe_preview:
            result["text"] = payload["text"]
        return result

    def send(self, payload: Mapping[str, Any], *, preview_digest: str):
        self.calls.append(("send", (dict(payload), preview_digest)))
        return {
            "delivery_id": "delivery-1",
            "status": "completed",
            "content_digest": "b" * 64,
        }

    def status(self, delivery_id: str):
        self.calls.append(("status", delivery_id))
        return {"delivery_id": delivery_id, "status": "completed"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="giga")
    root = parser.add_subparsers(dest="command")
    session = root.add_parser("session")
    session_subparsers = session.add_subparsers(dest="session_command")
    thread_commands.register(
        session_subparsers,
        argparse.ArgumentParser(add_help=False),
    )
    return parser


def _send_args(*extra: str) -> argparse.Namespace:
    return _parser().parse_args(
        [
            "session",
            "send",
            "thread-1",
            "--source",
            "codex",
            "--project-id",
            "project-1",
            "--text",
            "secret delivery text",
            "--expected-revision",
            "2026-08-04T12:00:00+00:00",
            "--active-turn",
            "turn-1",
            "--intent",
            "steer",
            "--idempotency-key",
            "delivery-key-1",
            "--expires-at",
            "2026-08-04T12:05:00+00:00",
            *extra,
        ]
    )


def test_cli_dry_run_json_previews_without_mutation_or_content_echo(
    tmp_path: Path, capsys
) -> None:
    actions = _Actions()
    handlers = ThreadRelayCommandHandlers(actions)

    assert (
        handlers.send(
            _send_args("--dry-run", "--json"), HarnessConfig(data_dir=tmp_path)
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["preview"]["preview_digest"] == PREVIEW_DIGEST
    assert "secret delivery text" not in json.dumps(payload)
    assert [name for name, _ in actions.calls] == ["preview"]


def test_cli_short_dry_run_derives_preview_only_delivery_guards(
    tmp_path: Path, capsys
) -> None:
    actions = _Actions()
    handlers = ThreadRelayCommandHandlers(actions)
    args = _parser().parse_args(
        [
            "session",
            "send",
            "thread-1",
            "--text",
            "review failing tests",
            "--dry-run",
            "--json",
        ]
    )

    assert handlers.send(args, HarnessConfig(data_dir=tmp_path)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert [name for name, _ in actions.calls] == ["read", "preview"]
    request = actions.calls[1][1]
    assert request["expected_target_revision"] == "2026-08-04T12:00:00+00:00"
    assert request["idempotency_key"] == "thread-relay-dry-run"
    assert request["expires_at"]


def test_cli_real_send_requires_explicit_delivery_guards(tmp_path: Path) -> None:
    actions = _Actions()
    handlers = ThreadRelayCommandHandlers(actions)
    args = _parser().parse_args(
        ["session", "send", "thread-1", "--text", "review failing tests"]
    )

    with pytest.raises(
        ValueError,
        match=(
            "thread relay send requires --expected-revision, "
            "--idempotency-key, and --expires-at"
        ),
    ):
        handlers.send(args, HarnessConfig(data_dir=tmp_path))

    assert actions.calls == []


def test_cli_send_previews_before_delivery_and_exposes_other_actions(
    tmp_path: Path, capsys
) -> None:
    actions = _Actions()
    handlers = ThreadRelayCommandHandlers(actions)
    config = HarnessConfig(data_dir=tmp_path)

    assert handlers.send(_send_args("--json"), config) == 0
    delivery = json.loads(capsys.readouterr().out)
    assert delivery["delivery"]["status"] == "completed"
    assert [name for name, _ in actions.calls] == ["preview", "send"]
    assert actions.calls[1][1][1] == PREVIEW_DIGEST

    parser = _parser()
    assert (
        handlers.list(
            parser.parse_args(
                ["session", "threads", "--project-id", "project-1", "--json"]
            ),
            config,
        )
        == 0
    )
    assert (
        handlers.read(
            parser.parse_args(
                [
                    "session",
                    "read",
                    "thread-1",
                    "--source",
                    "acp",
                    "--project-id",
                    "project-1",
                    "--json",
                ]
            ),
            config,
        )
        == 0
    )
    assert (
        handlers.status(
            parser.parse_args(
                [
                    "session",
                    "status",
                    "delivery-1",
                    "--project-id",
                    "project-1",
                    "--json",
                ]
            ),
            config,
        )
        == 0
    )
    assert [name for name, _ in actions.calls][-3:] == ["list", "read", "status"]


def _send_payload() -> dict[str, Any]:
    return {
        "source": "gigaloom",
        "project_id": "project-1",
        "source_thread_id": None,
        "thread_id": "thread-1",
        "text": "review the failing tests",
        "intent": "follow_up",
        "author_mode": "user_authored",
        "expected_target_revision": "revision-1",
        "expected_active_turn_id": None,
        "idempotency_key": "delivery-key-1",
        "expires_at": "2026-08-04T12:05:00Z",
        "attachment_refs": [],
    }


def test_route_local_api_lists_reads_previews_sends_and_reports_status() -> None:
    actions = _Actions()
    app = FastAPI()
    app.include_router(create_router(actions))
    client = TestClient(app)

    listed = client.get(
        "/api/thread-relay/threads",
        params={"source": "codex", "project_id": "project-1", "limit": 10},
    )
    read = client.get(
        "/api/thread-relay/threads/acp/thread-1",
        params={"project_id": "project-1"},
    )
    deliveries = client.get(
        "/api/thread-relay/threads/gigaloom/thread-1/deliveries",
        params={
            "project_id": "project-1",
            "direction": "incoming",
            "limit": 10,
        },
    )
    preview = client.post("/api/thread-relay/deliveries/preview", json=_send_payload())
    delivered = client.post("/api/thread-relay/deliveries", json=_send_payload())
    status = client.get(
        "/api/thread-relay/deliveries/delivery-1",
        params={"project_id": "project-1"},
    )

    assert listed.status_code == read.status_code == deliveries.status_code == 200
    assert deliveries.json()["direction"] == "incoming"
    assert preview.json()["dry_run"] is True
    assert delivered.json()["delivery"]["status"] == "completed"
    assert status.json() == {"delivery_id": "delivery-1", "status": "completed"}
    assert [name for name, _ in actions.calls] == [
        "list",
        "read",
        "deliveries",
        "preview",
        "preview",
        "send",
        "status",
    ]


def test_route_factory_binds_request_actor_and_explicit_project() -> None:
    actions = _Actions()
    scopes: list[tuple[str, str]] = []

    def factory(actor_scope: str, project_id: str) -> _Actions:
        scopes.append((actor_scope, project_id))
        return actions

    remote = FastAPI()

    @remote.middleware("http")
    async def bind_remote_actor(request, call_next):
        request.state.ui_actor = {"actor_id": "actor-remote-1"}
        return await call_next(request)

    remote.include_router(create_router(actions_factory=factory))
    response = TestClient(remote).get(
        "/api/thread-relay/threads",
        params={"project_id": "project-1"},
    )

    local = FastAPI()
    local.include_router(create_router(actions_factory=factory))
    local_response = TestClient(local).get(
        "/api/thread-relay/threads",
        params={"project_id": "project-2"},
    )

    assert response.status_code == local_response.status_code == 200
    assert scopes == [
        ("actor-remote-1", "project-1"),
        (LOCAL_THREAD_ACTOR_SCOPE, "project-2"),
    ]


def test_preview_content_echo_is_rejected_before_mutation() -> None:
    actions = _Actions(unsafe_preview=True)
    app = FastAPI()
    app.include_router(create_router(actions))

    response = TestClient(app).post(
        "/api/thread-relay/deliveries", json=_send_payload()
    )

    assert response.status_code == 400
    assert [name for name, _ in actions.calls] == ["preview"]
