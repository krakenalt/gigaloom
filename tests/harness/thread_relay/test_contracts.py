"""Thread Relay authority, boundedness, and codec contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from gigaloom.sessions.api import (
    MAX_THREAD_CURSOR_CHARS,
    MAX_THREAD_RELAY_DEPTH,
    MAX_THREAD_VISIBLE_MESSAGES,
    ThreadActiveTurnV1,
    ThreadAuthorMode,
    ThreadDeliveryIntent,
    ThreadDeliveryReceiptV1,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    ThreadReadProjectionV1,
    ThreadRelationshipKind,
    ThreadRelationshipV1,
    ThreadSourceKind,
    ThreadVisibleMessageV1,
    ThreadVisibleRole,
    thread_delivery_receipt_digest,
    thread_delivery_receipt_from_dict,
    thread_delivery_receipt_to_dict,
    thread_locator_digest,
    thread_locator_from_dict,
    thread_locator_to_dict,
    thread_message_content_digest,
    thread_message_envelope_digest,
    thread_message_envelope_from_dict,
    thread_message_envelope_to_dict,
    thread_read_projection_digest,
    thread_read_projection_from_dict,
    thread_read_projection_to_dict,
)


NOW = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)


def _locator(
    thread_id: str,
    *,
    source_kind: ThreadSourceKind = ThreadSourceKind.GIGALOOM,
    project_id: str = "project-1",
    actor_scope: str = "actor-1",
) -> ThreadLocatorV1:
    return ThreadLocatorV1(
        source_kind=source_kind,
        adapter_id=f"{source_kind.value}-adapter-v1",
        project_id=project_id,
        thread_id=thread_id,
        actor_scope=actor_scope,
        workspace_identity="workspace-fingerprint-1",
        provider_session_ref=(
            None if source_kind is ThreadSourceKind.GIGALOOM else "provider-session-1"
        ),
        capability_revision=f"{source_kind.value}-threads-v1",
    )


def _message(
    message_id: str = "message-1",
    *,
    content: str = "Visible <redacted> message",
    created_at: datetime = NOW,
    redacted: bool = True,
) -> ThreadVisibleMessageV1:
    return ThreadVisibleMessageV1(
        message_id=message_id,
        role=ThreadVisibleRole.ASSISTANT,
        content=content,
        content_digest=thread_message_content_digest(content),
        created_at=created_at,
        redacted=redacted,
    )


def _projection() -> ThreadReadProjectionV1:
    return ThreadReadProjectionV1(
        locator=_locator("target-thread"),
        title="Bounded target thread",
        status="active",
        updated_at=NOW + timedelta(minutes=1),
        visible_messages=(_message(),),
        active_turn=ThreadActiveTurnV1(
            turn_id="turn-7",
            status="running",
            revision="turn-revision-3",
        ),
        route="codex-gpt2giga-gigachat-max",
        model="GigaChat-2-Max",
        relationships=(
            ThreadRelationshipV1(
                kind=ThreadRelationshipKind.PARENT,
                locator=_locator("parent-thread"),
            ),
        ),
        next_cursor="cursor-page-2",
        omitted_count=4,
        redaction_facts=("secret_values_redacted",),
        unsupported_facts=("hidden_reasoning_excluded",),
    )


def _envelope(
    *,
    intent: ThreadDeliveryIntent = ThreadDeliveryIntent.FOLLOW_UP,
    active_turn_id: str | None = None,
) -> ThreadMessageEnvelopeV1:
    return ThreadMessageEnvelopeV1(
        source_locator=_locator("source-thread"),
        target_locator=_locator("target-thread"),
        actor_binding="actor-1",
        project_binding="project-1",
        role=ThreadVisibleRole.USER,
        author_mode=ThreadAuthorMode.AGENT_PROPOSED_USER_APPROVED,
        message_ref="message-ref-1",
        attachment_refs=("attachment-2", "attachment-1"),
        intent=intent,
        expected_target_revision="target-revision-4",
        expected_active_turn_id=active_turn_id,
        idempotency_key="relay-idempotency-1",
        expires_at=NOW + timedelta(minutes=10),
        depth=1,
    )


def _receipt() -> ThreadDeliveryReceiptV1:
    return ThreadDeliveryReceiptV1(
        delivery_id="delivery-1",
        source_identity=_locator("source-thread"),
        target_identity=_locator("target-thread"),
        action=ThreadDeliveryIntent.FOLLOW_UP,
        status=ThreadDeliveryStatus.COMPLETED,
        created_at=NOW,
        accepted_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=2),
        run_ref="run-1",
        job_ref="job-1",
        turn_ref="turn-8",
        content_digest=hashlib.sha256(b"redacted persisted content").hexdigest(),
        capability_revision="gigaloom-threads-v1",
        terminal_reason=None,
    )


def test_main_contracts_round_trip_strictly_with_stable_digests() -> None:
    locator = _locator("target-thread")
    projection = _projection()
    envelope = _envelope()
    receipt = _receipt()

    assert thread_locator_from_dict(thread_locator_to_dict(locator)) == locator
    assert (
        thread_read_projection_from_dict(thread_read_projection_to_dict(projection))
        == projection
    )
    assert (
        thread_message_envelope_from_dict(thread_message_envelope_to_dict(envelope))
        == envelope
    )
    assert (
        thread_delivery_receipt_from_dict(thread_delivery_receipt_to_dict(receipt))
        == receipt
    )
    assert len(thread_locator_digest(locator)) == 64
    assert thread_read_projection_digest(projection) == thread_read_projection_digest(
        replace(projection)
    )
    assert thread_message_envelope_digest(envelope) == thread_message_envelope_digest(
        replace(envelope)
    )
    assert thread_delivery_receipt_digest(receipt) == thread_delivery_receipt_digest(
        replace(receipt)
    )

    with pytest.raises(ValueError, match="unknown fields"):
        thread_locator_from_dict({**thread_locator_to_dict(locator), "raw_home": True})


def test_read_projection_bounds_content_cursor_order_and_redaction_facts() -> None:
    projection = _projection()

    with pytest.raises(ValueError, match="bounded tuple"):
        replace(
            projection,
            visible_messages=tuple(
                _message(f"message-{index}", redacted=False)
                for index in range(MAX_THREAD_VISIBLE_MESSAGES + 1)
            ),
            redaction_facts=(),
        )
    with pytest.raises(ValueError, match="next cursor"):
        replace(projection, next_cursor="x" * (MAX_THREAD_CURSOR_CHARS + 1))
    with pytest.raises(ValueError, match="chronological"):
        replace(
            projection,
            visible_messages=(
                _message("message-later", created_at=NOW + timedelta(seconds=1)),
                _message("message-earlier", created_at=NOW),
            ),
        )
    with pytest.raises(ValueError, match="require redaction facts"):
        replace(projection, redaction_facts=())
    with pytest.raises(ValueError, match="unsupported control"):
        _message(content="visible\x00secret")
    with pytest.raises(ValueError, match="role is invalid"):
        thread_read_projection_from_dict(
            {
                **thread_read_projection_to_dict(projection),
                "visible_messages": [
                    {
                        **thread_read_projection_to_dict(projection)[
                            "visible_messages"
                        ][0],
                        "role": "system",
                    }
                ],
            }
        )


def test_envelope_denies_cross_scope_non_user_unbounded_and_ambiguous_actions() -> None:
    envelope = _envelope()

    with pytest.raises(ValueError, match="actor binding"):
        replace(envelope, actor_binding="actor-2")
    with pytest.raises(ValueError, match="cross-actor or cross-project"):
        replace(envelope, source_locator=_locator("source", project_id="project-2"))
    with pytest.raises(ValueError, match="role must be user"):
        replace(envelope, role=ThreadVisibleRole.ASSISTANT)
    with pytest.raises(ValueError, match="depth exceeds"):
        replace(envelope, depth=MAX_THREAD_RELAY_DEPTH + 1)
    with pytest.raises(ValueError, match="idempotency key"):
        replace(envelope, idempotency_key="")
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(envelope, expires_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="only thread steer"):
        replace(envelope, expected_active_turn_id="turn-7")
    with pytest.raises(ValueError, match="exact active turn id"):
        _envelope(intent=ThreadDeliveryIntent.STEER)

    steer = _envelope(intent=ThreadDeliveryIntent.STEER, active_turn_id="turn-7")
    assert steer.expected_active_turn_id == "turn-7"


def test_receipt_is_digest_only_and_enforces_monotonic_terminal_states() -> None:
    receipt = _receipt()
    payload = thread_delivery_receipt_to_dict(receipt)

    assert payload["content_digest"] == receipt.content_digest
    assert "message" not in payload
    assert "content" not in payload
    with pytest.raises(ValueError, match="completion precedes acceptance"):
        replace(receipt, completed_at=NOW)
    with pytest.raises(ValueError, match="terminal thread receipt requires a reason"):
        replace(
            receipt,
            status=ThreadDeliveryStatus.FAILED,
            accepted_at=None,
            terminal_reason=None,
        )
    failed = replace(
        receipt,
        status=ThreadDeliveryStatus.FAILED,
        accepted_at=None,
        terminal_reason="target_revision_stale",
    )
    assert (
        thread_delivery_receipt_from_dict(thread_delivery_receipt_to_dict(failed))
        == failed
    )


def test_codec_rejects_tampered_content_digest_and_unknown_envelope_fields() -> None:
    projection_payload = thread_read_projection_to_dict(_projection())
    projection_payload["visible_messages"][0]["content"] = "tampered"
    with pytest.raises(ValueError, match="digest does not match"):
        thread_read_projection_from_dict(projection_payload)

    envelope_payload = thread_message_envelope_to_dict(_envelope())
    with pytest.raises(ValueError, match="unknown fields"):
        thread_message_envelope_from_dict(
            {**envelope_payload, "assistant_role_override": True}
        )
