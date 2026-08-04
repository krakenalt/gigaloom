"""Production route actions over the existing structured-session owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast, Protocol

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.execution.thread_relay.projections import (
    GigaLoomThreadProjector,
    ThreadRelayAuthorizationError,
    ThreadRelayUnsupportedError,
    ThreadSessionStorePort,
)
from gigaloom.execution.thread_relay.service import (
    GigaLoomStructuredThreadRelay,
    ThreadTurnSubmissionPort,
)
from gigaloom.sessions.api import (
    ThreadAuthorMode,
    ThreadDeliveryIntent,
    ThreadDeliveryCursor,
    ThreadDeliveryRepository,
    ThreadMessageEnvelopeV1,
    ThreadSourceKind,
    ThreadVisibleRole,
    thread_delivery_receipt_to_dict,
    thread_message_content_digest,
    thread_read_projection_to_dict,
)


Clock = Callable[[], datetime]


class ThreadRelayApprovalVerifierPort(Protocol):
    """Verify a content-free user approval at the durable action boundary."""

    def verify(
        self,
        receipt_ref: str,
        *,
        actor_scope: str,
        project_id: str,
        preview_digest: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class ThreadRelayScopeV1:
    """Explicit actor/project authority used to construct route actions."""

    actor_scope: str
    project_id: str

    def __post_init__(self) -> None:
        if not self.actor_scope or not self.project_id:
            raise ValueError("thread relay actor and project scope are required")


@dataclass(frozen=True, slots=True)
class _TransientMessageResolver:
    message_ref: str
    content: str

    def resolve(self, message_ref: str) -> str:
        if message_ref != self.message_ref:
            raise ThreadRelayAuthorizationError(
                "thread relay message reference is not permitted"
            )
        return self.content


class GigaLoomThreadRelayActions:
    """Bind CLI/API adapters to one local actor and project scope."""

    def __init__(
        self,
        *,
        scope: ThreadRelayScopeV1,
        session_store: ThreadSessionStorePort,
        delivery_repository: ThreadDeliveryRepository,
        turn_submitter: ThreadTurnSubmissionPort,
        approval_verifier: ThreadRelayApprovalVerifierPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.scope = scope
        self.session_store = session_store
        self.delivery_repository = delivery_repository
        self.turn_submitter = turn_submitter
        self.approval_verifier = approval_verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.projector = GigaLoomThreadProjector(
            actor_scope=scope.actor_scope,
            project_id=scope.project_id,
            session_store=session_store,
        )

    def list_threads(
        self,
        *,
        source: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        self._require_gigaloom_source(source)
        if cursor is not None:
            raise ThreadRelayUnsupportedError(
                "GigaLoom thread list cursors are not available"
            )
        page = self.projector.list_threads(limit=limit)
        return {
            "schema_version": 1,
            "source": ThreadSourceKind.GIGALOOM.value,
            "threads": [thread_read_projection_to_dict(item) for item in page.items],
            "next_cursor": None,
            "has_more": page.has_more,
            "omitted_count": page.omitted_count,
        }

    def read_thread(
        self,
        *,
        source: str,
        thread_id: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        self._require_gigaloom_source(source)
        session = self.projector.bound_session(thread_id)
        projection = self.projector.read_thread(
            self.projector.locator(session),
            cursor=cursor,
            limit=limit,
        )
        return {"thread": thread_read_projection_to_dict(projection)}

    def list_deliveries(
        self,
        *,
        source: str,
        thread_id: str,
        direction: str,
        cursor: str | None,
        limit: int,
    ) -> Mapping[str, Any]:
        """Return digest-only delivery states for one permitted thread."""
        self._require_gigaloom_source(source)
        session = self.projector.bound_session(thread_id)
        page = self.delivery_repository.list_for_thread(
            self.projector.locator(session),
            direction=direction,
            cursor=_delivery_cursor(cursor),
            limit=limit,
        )
        return {
            "schema_version": 1,
            "direction": direction,
            "items": [
                {
                    "direction": direction,
                    "envelope_digest": item.envelope_digest,
                    "receipt": thread_delivery_receipt_to_dict(item.receipt),
                }
                for item in page.items
            ],
            "next_cursor": (
                _delivery_cursor_text(page.next_cursor)
                if page.next_cursor is not None
                else None
            ),
            "has_more": page.has_more,
        }

    def preview_send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._preview(payload, allow_agent_approved=False)

    def preview_agent_send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Preview an agent proposal after a dedicated approval surface binds it."""
        return self._preview(payload, allow_agent_approved=True)

    def _preview(
        self,
        payload: Mapping[str, Any],
        *,
        allow_agent_approved: bool,
    ) -> Mapping[str, Any]:
        envelope, relay = self._delivery(
            payload,
            allow_agent_approved=allow_agent_approved,
        )
        preview = relay.preview(envelope, now=self._now())
        facts = {
            "envelope_digest": preview.envelope_digest,
            "content_digest": preview.content_digest,
            "target_revision": preview.target_revision,
            "intent": preview.intent.value,
            "expires_at": preview.expires_at.isoformat(),
            "redacted": preview.redacted,
        }
        return {"preview_digest": canonical_digest(facts), **facts}

    def send(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
    ) -> Mapping[str, Any]:
        return self._send(
            payload,
            preview_digest=preview_digest,
            allow_agent_approved=False,
        )

    def send_agent_approved(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
        approval_receipt_ref: str,
    ) -> Mapping[str, Any]:
        """Deliver an agent proposal already admitted by the tool approval owner."""
        verifier = self.approval_verifier
        if verifier is None:
            raise ThreadRelayUnsupportedError(
                "agent-proposed relay approval verifier is unavailable"
            )
        if not verifier.verify(
            approval_receipt_ref,
            actor_scope=self.scope.actor_scope,
            project_id=self.scope.project_id,
            preview_digest=preview_digest,
        ):
            raise ThreadRelayAuthorizationError(
                "agent-proposed relay approval receipt is not valid"
            )
        return self._send(
            payload,
            preview_digest=preview_digest,
            allow_agent_approved=True,
        )

    def _send(
        self,
        payload: Mapping[str, Any],
        *,
        preview_digest: str,
        allow_agent_approved: bool,
    ) -> Mapping[str, Any]:
        current = self._preview(
            payload,
            allow_agent_approved=allow_agent_approved,
        )
        if current["preview_digest"] != preview_digest:
            raise ThreadRelayAuthorizationError(
                "thread relay preview changed; review the delivery again"
            )
        envelope, relay = self._delivery(
            payload,
            allow_agent_approved=allow_agent_approved,
        )
        outcome = relay.deliver(envelope, now=self._now())
        return {
            "receipt": thread_delivery_receipt_to_dict(outcome.record.receipt),
            "idempotent_replay": outcome.idempotent_replay,
        }

    def status(self, delivery_id: str) -> Mapping[str, Any]:
        record = self.delivery_repository.get(delivery_id)
        if (
            record.envelope.actor_binding != self.scope.actor_scope
            or record.envelope.project_binding != self.scope.project_id
        ):
            raise ThreadRelayAuthorizationError(
                "thread delivery scope is not permitted"
            )
        return {
            "receipt": thread_delivery_receipt_to_dict(record.receipt),
            "envelope_digest": record.envelope_digest,
        }

    def _delivery(
        self,
        payload: Mapping[str, Any],
        *,
        allow_agent_approved: bool,
    ) -> tuple[ThreadMessageEnvelopeV1, GigaLoomStructuredThreadRelay]:
        self._require_gigaloom_source(str(payload.get("source") or "gigaloom"))
        author_mode = ThreadAuthorMode(
            str(payload.get("author_mode") or ThreadAuthorMode.USER_AUTHORED.value)
        )
        if (
            author_mode is not ThreadAuthorMode.USER_AUTHORED
            and not allow_agent_approved
        ):
            raise ThreadRelayUnsupportedError(
                "agent-proposed relay requires the dedicated approval surface"
            )
        target = self.projector.bound_session(_text(payload, "thread_id"))
        content = _text(payload, "text")
        content_digest = thread_message_content_digest(content)
        message_ref = f"relay-message:{content_digest}"
        source_thread_id = _optional_text(payload.get("source_thread_id"))
        source_locator = None
        if source_thread_id is not None:
            source = self.projector.bound_session(source_thread_id)
            source_locator = self.projector.locator(source)
        intent = ThreadDeliveryIntent(
            str(payload.get("intent") or ThreadDeliveryIntent.MESSAGE.value)
        )
        envelope = ThreadMessageEnvelopeV1(
            source_locator=source_locator,
            target_locator=self.projector.locator(target),
            actor_binding=self.scope.actor_scope,
            project_binding=self.scope.project_id,
            role=ThreadVisibleRole.USER,
            author_mode=author_mode,
            message_ref=message_ref,
            attachment_refs=tuple(_text_list(payload.get("attachment_refs"))),
            intent=intent,
            expected_target_revision=_text(payload, "expected_target_revision"),
            expected_active_turn_id=_optional_text(
                payload.get("expected_active_turn_id")
            ),
            idempotency_key=_text(payload, "idempotency_key"),
            expires_at=_timestamp(payload.get("expires_at")),
            depth=0 if source_locator is None else 1,
        )
        return envelope, GigaLoomStructuredThreadRelay(
            actor_scope=self.scope.actor_scope,
            project_id=self.scope.project_id,
            session_store=self.session_store,
            delivery_repository=self.delivery_repository,
            turn_submitter=self.turn_submitter,
            message_resolver=_TransientMessageResolver(message_ref, content),
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("thread relay clock must be timezone-aware")
        return value

    @staticmethod
    def _require_gigaloom_source(source: str) -> None:
        try:
            parsed = ThreadSourceKind(source)
        except ValueError as error:
            raise ThreadRelayUnsupportedError(
                "thread source capability is unavailable"
            ) from error
        if parsed is not ThreadSourceKind.GIGALOOM:
            raise ThreadRelayUnsupportedError(
                f"{parsed.value} thread actions require a configured live adapter"
            )


def build_thread_relay_actions(
    *,
    actor_scope: str,
    project_id: str,
    session_store: ThreadSessionStorePort,
    data_dir: str,
    turn_submitter: ThreadTurnSubmissionPort,
    approval_verifier: ThreadRelayApprovalVerifierPort | None = None,
) -> GigaLoomThreadRelayActions:
    """Construct production actions without introducing another state owner."""
    return GigaLoomThreadRelayActions(
        scope=ThreadRelayScopeV1(actor_scope, project_id),
        session_store=session_store,
        delivery_repository=ThreadDeliveryRepository(data_dir),
        turn_submitter=turn_submitter,
        approval_verifier=approval_verifier,
    )


def _text(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"thread relay {name} is required")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("thread relay optional identity is invalid")
    return value.strip()


def _text_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError("thread relay attachment refs are invalid")
    return tuple(cast(str, item) for item in value)


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("thread relay expires_at is required")
    if parsed.tzinfo is None:
        raise ValueError("thread relay expires_at must be timezone-aware")
    return parsed


def _delivery_cursor(value: str | None) -> ThreadDeliveryCursor | None:
    if value is None:
        return None
    if len(value) > 1024 or "|" not in value:
        raise ValueError("thread delivery cursor is invalid")
    timestamp, delivery_id = value.rsplit("|", 1)
    parsed = _timestamp(timestamp)
    if not delivery_id:
        raise ValueError("thread delivery cursor is invalid")
    return ThreadDeliveryCursor(parsed, delivery_id)


def _delivery_cursor_text(value: ThreadDeliveryCursor) -> str:
    return f"{value.created_at.isoformat()}|{value.delivery_id}"


__all__ = [
    "GigaLoomThreadRelayActions",
    "ThreadRelayApprovalVerifierPort",
    "ThreadRelayScopeV1",
    "build_thread_relay_actions",
]
