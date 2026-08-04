from __future__ import annotations

from typing import Any, Mapping

import pytest

from gigaloom.tools import (
    THREAD_RELAY_TOOL_IDS,
    RestrictedThreadRelayTools,
    ThreadRelayToolScope,
)


PREVIEW_DIGEST = "a" * 64


class _Actions:
    def __init__(
        self,
        *,
        unsafe_preview: bool = False,
        approval_allowed: bool = True,
    ) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.unsafe_preview = unsafe_preview
        self.approval_allowed = approval_allowed

    def list_threads(self, *, source: str, cursor: str | None, limit: int):
        self.calls.append(("list", (source, cursor, limit)))
        return {"threads": [], "next_cursor": None}

    def read_thread(
        self,
        *,
        source: str,
        thread_id: str,
        cursor: str | None,
        limit: int,
    ):
        self.calls.append(("read", (source, thread_id, cursor, limit)))
        return {"thread": {"locator": {"thread_id": thread_id}}}

    def preview_agent_send(self, payload: Mapping[str, Any]):
        self.calls.append(("preview", dict(payload)))
        preview = {
            "preview_digest": PREVIEW_DIGEST,
            "content_digest": "b" * 64,
            "target_revision": payload["expected_target_revision"],
        }
        if self.unsafe_preview:
            preview["text"] = payload["text"]
        return preview

    def send_agent_approved(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
        approval_receipt_ref: str,
    ):
        self.calls.append(
            ("send", (dict(payload), preview_digest, approval_receipt_ref))
        )
        if not self.approval_allowed:
            raise PermissionError("thread tool approval receipt is not valid")
        return {"receipt": {"delivery_id": "delivery-1", "status": "accepted"}}

    def status(self, delivery_id: str):
        self.calls.append(("status", delivery_id))
        return {
            "receipt": {"delivery_id": delivery_id, "status": "cancelled"},
            "envelope_digest": "c" * 64,
        }


def _surface(
    *,
    actions: _Actions | None = None,
) -> tuple[RestrictedThreadRelayTools, _Actions]:
    bound_actions = actions or _Actions()
    return (
        RestrictedThreadRelayTools(
            scope=ThreadRelayToolScope("actor-1", "project-1"),
            actions=bound_actions,
        ),
        bound_actions,
    )


def _send_arguments(**extra: Any) -> dict[str, Any]:
    return {
        "source": "gigaloom",
        "source_thread_id": "thread-source",
        "thread_id": "thread-target",
        "text": "Review secret token=do-not-echo",
        "intent": "follow_up",
        "expected_target_revision": "revision-7",
        "expected_active_turn_id": None,
        "idempotency_key": "idempotency-1",
        "expires_at": "2026-08-04T13:00:00Z",
        "attachment_refs": [],
        **extra,
    }


def test_surface_exposes_only_four_bounded_tools_without_scope_or_admin_inputs() -> (
    None
):
    surface, _ = _surface()
    descriptors = surface.list_tools()

    assert tuple(item.id for item in descriptors) == THREAD_RELAY_TOOL_IDS
    encoded = repr([item.input_schema for item in descriptors])
    for forbidden in (
        "actor_scope",
        "project_id",
        "raw_history",
        "settings",
        "secrets",
        "apply",
        "install",
        "publication",
        "recovery",
    ):
        assert forbidden not in encoded


def test_list_read_and_status_use_the_bound_actions_and_preserve_cancellation() -> None:
    surface, actions = _surface()

    assert surface.call_tool("thread.list", {"limit": 10})["threads"] == []
    assert (
        surface.call_tool(
            "thread.read",
            {"source": "gigaloom", "thread_id": "thread-1", "limit": 20},
        )["thread"]["locator"]["thread_id"]
        == "thread-1"
    )
    status = surface.call_tool("thread.status", {"delivery_id": "delivery-1"})

    assert status["receipt"]["status"] == "cancelled"
    assert [name for name, _ in actions.calls] == ["list", "read", "status"]
    with pytest.raises(ValueError, match="not admitted"):
        surface.call_tool("thread.list", {"project_id": "other-project"})
    with pytest.raises(ValueError, match="not admitted"):
        surface.call_tool("thread.raw_history", {})


def test_send_is_preview_only_until_exact_scoped_approval_is_verified() -> None:
    surface, actions = _surface()

    preview = surface.call_tool("thread.send", _send_arguments())

    assert preview["requires_user_approval"] is True
    assert "do-not-echo" not in repr(preview)
    assert [name for name, _ in actions.calls] == ["preview"]

    delivered = surface.call_tool(
        "thread.send",
        _send_arguments(
            preview_digest=PREVIEW_DIGEST,
            approval_receipt_ref="approval-1",
        ),
    )

    assert delivered["delivery"]["receipt"]["status"] == "accepted"
    assert actions.calls[-1][0] == "send"
    sent_payload, sent_digest, approval_ref = actions.calls[-1][1]
    assert sent_payload["author_mode"] == "agent_proposed_user_approved"
    assert sent_payload["source_thread_id"] == "thread-source"
    assert sent_payload["idempotency_key"] == "idempotency-1"
    assert sent_payload["expires_at"] == "2026-08-04T13:00:00Z"
    assert sent_digest == PREVIEW_DIGEST
    assert approval_ref == "approval-1"


def test_send_fails_closed_on_stale_preview_invalid_approval_or_content_echo() -> None:
    denied, denied_actions = _surface(actions=_Actions(approval_allowed=False))
    with pytest.raises(PermissionError, match="not valid"):
        denied.call_tool(
            "thread.send",
            _send_arguments(
                preview_digest=PREVIEW_DIGEST,
                approval_receipt_ref="approval-denied",
            ),
        )
    assert [name for name, _ in denied_actions.calls] == ["preview", "send"]

    stale, stale_actions = _surface()
    with pytest.raises(PermissionError, match="preview changed"):
        stale.call_tool(
            "thread.send",
            _send_arguments(
                preview_digest="f" * 64,
                approval_receipt_ref="approval-1",
            ),
        )
    assert [name for name, _ in stale_actions.calls] == ["preview"]

    unsafe, unsafe_actions = _surface(actions=_Actions(unsafe_preview=True))
    with pytest.raises(ValueError, match="must not echo"):
        unsafe.call_tool("thread.send", _send_arguments())
    assert [name for name, _ in unsafe_actions.calls] == ["preview"]
