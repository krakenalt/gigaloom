from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from gigaloom.execution.thread_relay import (
    AcpThreadRelayAdapter,
    CodexThreadRelayAdapter,
    ThreadProviderCapabilityState,
    ThreadProviderOperation,
)
from gigaloom.harnesses.acp.api import AcpSessionPageV1
from gigaloom.sessions.api import ThreadSourceKind


NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


class _CodexClient:
    runtime_id = "codex-runtime-1"
    alive = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(
        self, method: str, params: Mapping[str, Any], *, timeout: float
    ) -> Mapping[str, Any]:
        assert timeout > 0
        self.calls.append((method, dict(params)))
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thread-1",
                        "preview": "Review relay",
                        "createdAt": 1785844800,
                        "updatedAt": 1785844800,
                        "status": {"type": "idle"},
                        "modelProvider": "openai",
                    }
                ],
                "nextCursor": "next-page",
            }
        if method == "thread/read":
            return {
                "thread": {
                    "id": params["threadId"],
                    "preview": "Review relay",
                    "createdAt": 1785844800,
                    "updatedAt": 1785844800,
                    "status": {"type": "idle"},
                    "modelProvider": "openai",
                    "turns": [
                        {
                            "id": "turn-1",
                            "status": "inProgress",
                            "items": [
                                {
                                    "id": "message-user-1",
                                    "type": "userMessage",
                                    "text": "Check token=visible-secret-value",
                                },
                                {
                                    "id": "reasoning-1",
                                    "type": "reasoning",
                                    "text": "private chain",
                                },
                                {
                                    "id": "message-agent-1",
                                    "type": "agentMessage",
                                    "text": "Working",
                                },
                            ],
                        }
                    ],
                }
            }
        if method == "turn/start":
            return {"turn": {"id": "turn-2", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        raise AssertionError(method)

    def next_message(self, *, timeout: float) -> Mapping[str, Any] | None:
        del timeout
        return None

    def respond(self, request_id, *, result=None, error=None) -> None:
        del request_id, result, error

    def close(self) -> None:
        return None


def test_codex_adapter_uses_pinned_public_methods_and_redacts_visible_reads() -> None:
    client = _CodexClient()
    adapter = CodexThreadRelayAdapter(
        actor_scope="actor-1",
        project_id="project-1",
        workspace_identity="sha256:" + "b" * 64,
        client=client,
    )

    page = adapter.list_threads(limit=10)
    locator = page.items[0].locator
    read = adapter.read_thread(locator, limit=10)

    assert page.next_cursor == "next-page"
    assert locator.source_kind is ThreadSourceKind.CODEX
    assert read.projection is not None
    projection = read.projection
    assert [item.content for item in projection.visible_messages] == [
        "Check token=<redacted>",
        "Working",
    ]
    assert projection.active_turn is not None
    assert projection.active_turn.turn_id == "turn-1"
    assert "hidden_reasoning_excluded" in projection.unsupported_facts
    assert all(
        fact.state is ThreadProviderCapabilityState.SUPPORTED
        for fact in adapter.capabilities().facts
    )

    started = adapter.start_turn(
        locator,
        content="Start follow-up",
        idempotency_key="delivery-1",
        expected_target_revision=projection.updated_at.isoformat(),
    )
    steered = adapter.steer_turn(
        locator,
        active_turn_id="turn-1",
        content="Change direction",
        idempotency_key="delivery-2",
        expected_target_revision=projection.updated_at.isoformat(),
    )

    assert started.accepted is True and started.turn_ref == "turn-2"
    assert steered.accepted is True and steered.turn_ref == "turn-1"
    assert [method for method, _ in client.calls] == [
        "thread/list",
        "thread/read",
        "thread/read",
        "turn/start",
        "thread/read",
        "turn/steer",
    ]
    assert client.calls[-1][1] == {
        "threadId": "thread-1",
        "expectedTurnId": "turn-1",
        "input": [{"type": "text", "text": "Change direction"}],
        "clientUserMessageId": "delivery-2",
    }


def _acp_client(features: tuple[str, ...]):
    snapshot = SimpleNamespace(
        snapshot_digest="a" * 64,
        negotiated_features=tuple(
            SimpleNamespace(feature=feature) for feature in features
        ),
    )
    return SimpleNamespace(
        capability_snapshot=snapshot,
        route_identity=SimpleNamespace(route_id="gemini.acp"),
    )


def test_acp_adapter_uses_only_advertised_list_load_and_prompt(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    client = _acp_client(("session_list", "session_load", "structured_prompt"))

    def list_fn(_client, **kwargs):
        calls.append(("list", str(kwargs["cursor"])))
        return AcpSessionPageV1(("acp-session-1",), "next-acp", False)

    def load_fn(_client, **kwargs):
        calls.append(("load", kwargs["session_id"]))
        return SimpleNamespace(acp_session_id=kwargs["session_id"])

    def prompt_fn(_client, _binding, **kwargs):
        calls.append(("prompt", kwargs["text"]))
        return SimpleNamespace(generation=7)

    adapter = AcpThreadRelayAdapter(
        actor_scope="actor-1",
        project_id="project-1",
        workspace=tmp_path,
        workspace_identity="sha256:" + "c" * 64,
        client=client,
        clock=lambda: NOW,
        list_sessions_fn=list_fn,
        load_session_fn=load_fn,
        begin_prompt_fn=prompt_fn,
    )

    page = adapter.list_threads(cursor="cursor-1", limit=10)
    locator = page.items[0].locator
    read = adapter.read_thread(locator)
    started = adapter.start_turn(
        locator,
        content="Review this",
        idempotency_key="delivery-1",
        expected_target_revision=locator.capability_revision,
    )
    unsupported = adapter.steer_turn(
        locator,
        active_turn_id="turn-1",
        content="Do not emulate",
        idempotency_key="delivery-2",
        expected_target_revision=locator.capability_revision,
    )

    assert locator.source_kind is ThreadSourceKind.ACP
    assert read.projection is not None
    assert read.projection.visible_messages == ()
    assert started.accepted is True and started.turn_ref == "acp-generation-7"
    assert unsupported.accepted is False
    assert unsupported.unsupported == adapter.capabilities().fact(
        ThreadProviderOperation.STEER
    )
    assert calls == [
        ("list", "cursor-1"),
        ("load", "acp-session-1"),
        ("prompt", "Review this"),
    ]


def test_acp_unadvertised_methods_return_facts_without_wire_calls(
    tmp_path: Path,
) -> None:
    client = _acp_client(("structured_prompt",))
    adapter = AcpThreadRelayAdapter(
        actor_scope="actor-1",
        project_id="project-1",
        workspace=tmp_path,
        workspace_identity=None,
        client=client,
        clock=lambda: NOW,
        list_sessions_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("session/list must not be called")
        ),
    )

    page = adapter.list_threads()
    list_fact = page.capabilities.fact(ThreadProviderOperation.LIST)

    assert page.items == ()
    assert list_fact.state is ThreadProviderCapabilityState.UNSUPPORTED
    assert page.capabilities.fact(ThreadProviderOperation.READ).state is (
        ThreadProviderCapabilityState.UNSUPPORTED
    )
    assert page.capabilities.fact(ThreadProviderOperation.START).state is (
        ThreadProviderCapabilityState.UNSUPPORTED
    )


def test_provider_adapters_do_not_scrape_private_home_or_jsonl() -> None:
    root = Path(__file__).parents[3] / "src/gigaloom/execution/thread_relay"
    sources = "\n".join(
        (root / name).read_text(encoding="utf-8")
        for name in ("codex_adapter.py", "acp_adapter.py")
    )

    assert "Path.home" not in sources
    assert "sessions.jsonl" not in sources
    assert '".codex"' not in sources
    assert '".claude"' not in sources
