"""GigaLoom-owned structured-session Thread Relay orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, cast

from gigaloom.execution.thread_relay.projections import (
    GigaLoomThreadListPage,
    GigaLoomThreadProjector,
    ThreadRelayAuthorizationError,
    ThreadRelayTargetStateError,
    ThreadRelayUnsupportedError,
    ThreadSessionStorePort,
    optional_identity,
)
from gigaloom.sessions.api import (
    HarnessSession,
    ThreadDeliveryConflictError,
    ThreadDeliveryCursor,
    ThreadDeliveryIntent,
    ThreadDeliveryPage,
    ThreadDeliveryRecord,
    ThreadDeliveryRepository,
    ThreadDeliveryStatus,
    ThreadLocatorV1,
    ThreadMessageEnvelopeV1,
    ThreadReadProjectionV1,
    thread_locator_digest,
    thread_message_content_digest,
    thread_message_envelope_digest,
)
from gigaloom.types import redact_secrets


class ThreadTurnSubmissionPort(Protocol):
    """Existing application turn dispatcher used for target mutation."""

    def submit_turn(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        origin: str = "interactive",
    ) -> object:
        """Enqueue one user turn and durable job through existing owners."""


class ThreadMessageResolverPort(Protocol):
    """Resolve an approved message reference without copying source history."""

    def resolve(self, message_ref: str) -> str:
        """Return the user-approved content for one reference."""


class ThreadSteerPort(Protocol):
    """Optional exact-active-turn structured steering boundary."""

    def steer(
        self,
        *,
        session_id: str,
        turn_id: str,
        content: str,
        idempotency_key: str,
    ) -> None:
        """Steer one exact active turn without replay emulation."""


@dataclass(frozen=True, slots=True)
class ThreadDeliveryPreview:
    """Redacted immutable binding shown before a target mutation."""

    envelope_digest: str
    content_digest: str
    target_revision: str
    intent: ThreadDeliveryIntent
    expires_at: datetime
    redacted: bool
    attachment_omissions: tuple[Mapping[str, str], ...]


@dataclass(frozen=True, slots=True)
class ThreadDeliveryOutcome:
    """Final delivery record plus idempotent-replay evidence."""

    record: ThreadDeliveryRecord
    idempotent_replay: bool


class GigaLoomStructuredThreadRelay:
    """Bounded relay adapter for GigaLoom-owned durable sessions."""

    def __init__(
        self,
        *,
        actor_scope: str,
        project_id: str,
        session_store: ThreadSessionStorePort,
        delivery_repository: ThreadDeliveryRepository,
        turn_submitter: ThreadTurnSubmissionPort,
        message_resolver: ThreadMessageResolverPort,
        steer_port: ThreadSteerPort | None = None,
    ) -> None:
        if not actor_scope or not project_id:
            raise ValueError("thread relay actor and project bindings are required")
        self.actor_scope = actor_scope
        self.project_id = project_id
        self.session_store = session_store
        self.projector = GigaLoomThreadProjector(
            actor_scope=actor_scope,
            project_id=project_id,
            session_store=session_store,
        )
        self.delivery_repository = delivery_repository
        self.turn_submitter = turn_submitter
        self.message_resolver = message_resolver
        self.steer_port = steer_port

    def list_threads(self, *, limit: int = 50) -> GigaLoomThreadListPage:
        """List bounded lightweight projections without loading transcripts."""
        return self.projector.list_threads(limit=limit)

    def read_thread(
        self,
        locator: ThreadLocatorV1,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> ThreadReadProjectionV1:
        """Read one bounded redacted visible-message window."""
        return self.projector.read_thread(locator, cursor=cursor, limit=limit)

    def preview(
        self, envelope: ThreadMessageEnvelopeV1, *, now: datetime
    ) -> ThreadDeliveryPreview:
        """Resolve and redact one envelope without persisting or mutating."""
        session, _content, digest, redacted = self._prepare_delivery(envelope, now=now)
        return ThreadDeliveryPreview(
            envelope_digest=thread_message_envelope_digest(envelope),
            content_digest=digest,
            target_revision=session.updated_at,
            intent=envelope.intent,
            expires_at=envelope.expires_at,
            redacted=redacted,
            attachment_omissions=_attachment_omissions(envelope.attachment_refs),
        )

    def deliver(
        self,
        envelope: ThreadMessageEnvelopeV1,
        *,
        now: datetime,
    ) -> ThreadDeliveryOutcome:
        """Deliver one referenced user message through existing session/job owners."""
        session, content, content_digest, _redacted = self._prepare_delivery(
            envelope,
            now=now,
        )
        reservation = self.delivery_repository.reserve(
            envelope,
            content_digest=content_digest,
            observed_target_revision=session.updated_at,
            now=now,
        )
        current = reservation.record
        if current.receipt.status in {
            ThreadDeliveryStatus.COMPLETED,
            ThreadDeliveryStatus.FAILED,
            ThreadDeliveryStatus.EXPIRED,
            ThreadDeliveryStatus.CANCELLED,
        }:
            return ThreadDeliveryOutcome(current, idempotent_replay=True)
        if current.receipt.status is ThreadDeliveryStatus.ACCEPTED:
            raise ThreadRelayTargetStateError(
                "accepted thread delivery outcome is ambiguous; recovery required"
            )
        accepted = self.delivery_repository.transition(
            current.receipt.delivery_id,
            ThreadDeliveryStatus.ACCEPTED,
            expected_status=ThreadDeliveryStatus.PENDING,
            now=now,
        )
        try:
            completed = (
                self._steer(accepted, session=session, content=content, now=now)
                if envelope.intent is ThreadDeliveryIntent.STEER
                else self._submit_turn(
                    accepted,
                    session=session,
                    content=content,
                    now=now,
                )
            )
        except Exception:
            self._fail_accepted(accepted, now=now)
            raise
        return ThreadDeliveryOutcome(
            completed,
            idempotent_replay=not reservation.created,
        )

    def cancel_pending(
        self,
        delivery_id: str,
        *,
        now: datetime,
    ) -> ThreadDeliveryRecord:
        """Cancel one still-pending actor/project-bound delivery."""
        record = self.delivery_repository.get(delivery_id)
        self._require_envelope_scope(record.envelope)
        if record.receipt.status is not ThreadDeliveryStatus.PENDING:
            raise ThreadRelayTargetStateError(
                "only a pending delivery can be cancelled"
            )
        return self.delivery_repository.transition(
            delivery_id,
            ThreadDeliveryStatus.CANCELLED,
            expected_status=ThreadDeliveryStatus.PENDING,
            now=now,
            terminal_reason="delivery_cancelled",
        )

    def expire_pending(
        self, *, now: datetime, limit: int = 100
    ) -> tuple[ThreadDeliveryRecord, ...]:
        """Expire only pending deliveries bound to this actor and project."""
        return self.delivery_repository.expire_due(
            now=now,
            limit=limit,
            actor_binding=self.actor_scope,
            project_binding=self.project_id,
        )

    def deliveries(
        self,
        locator: ThreadLocatorV1,
        *,
        direction: str,
        cursor: ThreadDeliveryCursor | None = None,
        limit: int = 50,
    ) -> ThreadDeliveryPage:
        """List bounded incoming or outgoing delivery receipts for one thread."""
        self.projector.require_locator(locator)
        return self.delivery_repository.list_for_thread(
            locator,
            direction=direction,
            cursor=cursor,
            limit=limit,
        )

    def _prepare_delivery(
        self,
        envelope: ThreadMessageEnvelopeV1,
        *,
        now: datetime,
    ) -> tuple[HarnessSession, str, str, bool]:
        self._require_envelope_scope(envelope)
        if envelope.expires_at <= now:
            raise ThreadRelayTargetStateError("thread delivery envelope is expired")
        session = self.projector.bound_session(envelope.target_locator.thread_id)
        if session.updated_at != envelope.expected_target_revision:
            raise ThreadRelayTargetStateError("thread target revision changed")
        content = self.message_resolver.resolve(envelope.message_ref)
        redacted = redact_secrets(content)
        if not isinstance(redacted, str) or not redacted.strip():
            raise ValueError("thread relay message content is empty")
        digest = thread_message_content_digest(redacted)
        return session, redacted, digest, redacted != content

    def _submit_turn(
        self,
        record: ThreadDeliveryRecord,
        *,
        session: HarnessSession,
        content: str,
        now: datetime,
    ) -> ThreadDeliveryRecord:
        envelope = record.envelope
        relay_provenance = {
            "schema_version": 1,
            "delivery_id": record.receipt.delivery_id,
            "source_locator_digest": (
                thread_locator_digest(envelope.source_locator)
                if envelope.source_locator is not None
                else None
            ),
            "target_locator_digest": thread_locator_digest(envelope.target_locator),
            "intent": envelope.intent.value,
            "author_mode": envelope.author_mode.value,
            "content_digest": record.receipt.content_digest,
            "attachment_refs": list(envelope.attachment_refs),
            "attachment_omissions": list(
                _attachment_omissions(envelope.attachment_refs)
            ),
        }
        submission = self.turn_submitter.submit_turn(
            session.id,
            {
                "harness_id": session.default_harness_id,
                "model": session.default_model,
                "prompt": content,
                "extra": {"thread_relay": relay_provenance},
            },
            idempotency_key=f"thread-relay:{envelope.idempotency_key}",
            origin="interactive",
        )
        job, run, message = _submission_identities(submission)
        return self.delivery_repository.transition(
            record.receipt.delivery_id,
            ThreadDeliveryStatus.COMPLETED,
            expected_status=ThreadDeliveryStatus.ACCEPTED,
            now=now,
            job_ref=job,
            run_ref=run,
            turn_ref=message,
        )

    def _steer(
        self,
        record: ThreadDeliveryRecord,
        *,
        session: HarnessSession,
        content: str,
        now: datetime,
    ) -> ThreadDeliveryRecord:
        if self.steer_port is None:
            raise ThreadRelayUnsupportedError("structured thread steer is unavailable")
        active = self.projector.active_turn(session)
        expected = record.envelope.expected_active_turn_id
        if active is None or active.turn_id != expected:
            raise ThreadRelayTargetStateError("thread active turn changed")
        self.steer_port.steer(
            session_id=session.id,
            turn_id=active.turn_id,
            content=content,
            idempotency_key=record.envelope.idempotency_key,
        )
        return self.delivery_repository.transition(
            record.receipt.delivery_id,
            ThreadDeliveryStatus.COMPLETED,
            expected_status=ThreadDeliveryStatus.ACCEPTED,
            now=now,
            turn_ref=active.turn_id,
        )

    def _fail_accepted(
        self,
        record: ThreadDeliveryRecord,
        *,
        now: datetime,
    ) -> None:
        try:
            self.delivery_repository.transition(
                record.receipt.delivery_id,
                ThreadDeliveryStatus.FAILED,
                expected_status=ThreadDeliveryStatus.ACCEPTED,
                now=now,
                terminal_reason="delivery_target_mutation_failed",
            )
        except ThreadDeliveryConflictError:
            return

    def _require_envelope_scope(self, envelope: ThreadMessageEnvelopeV1) -> None:
        self.projector.require_locator(envelope.target_locator)
        if (
            envelope.actor_binding != self.actor_scope
            or envelope.project_binding != self.project_id
        ):
            raise ThreadRelayAuthorizationError(
                "thread envelope scope is not permitted"
            )


def _submission_identities(value: object) -> tuple[str, str, str]:
    job = getattr(value, "job", None)
    queued = getattr(value, "queued", None)
    run = getattr(queued, "run", None)
    message = getattr(queued, "user_message", None)
    identities = (
        optional_identity(getattr(job, "id", None)),
        optional_identity(getattr(run, "id", None)),
        optional_identity(getattr(message, "id", None)),
    )
    if any(identity is None for identity in identities):
        raise ThreadRelayUnsupportedError(
            "thread turn submitter returned incomplete durable identities"
        )
    return cast(tuple[str, str, str], identities)


def _attachment_omissions(
    attachment_refs: tuple[str, ...],
) -> tuple[Mapping[str, str], ...]:
    """Make unsupported attachment transfer explicit without reading content."""
    return tuple(
        {
            "attachment_ref": attachment_ref,
            "reason_id": "attachment_transfer_unavailable",
            "status": "not_transferred",
        }
        for attachment_ref in attachment_refs
    )
