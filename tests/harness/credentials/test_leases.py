"""Atomic bounded credential lease behavior."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Lock

from gigaloom.contracts.credentials import (
    CredentialLeaseDecisionStatus,
    CredentialLeaseRequestV1,
    CredentialLeaseStatus,
)
from gigaloom.runtime.credentials import (
    CredentialQuotaMetadata,
    CredentialQuotaStatus,
    CredentialSourceRegistration,
    CredentialSourceScope,
    InMemoryCredentialBroker,
    credential_action_scope_digest,
)
from gigaloom.secrets import SecretReference, SecretReferenceKind


POLICY_DIGEST = "a" * 64


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _Ids:
    def __init__(self) -> None:
        self._values = count(1)
        self._lock = Lock()

    def __call__(self, prefix: str) -> str:
        with self._lock:
            return f"{prefix}-{next(self._values)}"


def test_concurrent_requests_cannot_exceed_source_headroom() -> None:
    clock = _Clock()
    broker, reference = _broker(clock, max_active_leases=1)

    def request(index: int):
        return broker.request_lease(
            _request(
                reference,
                clock,
                request_id=f"request-{index}",
            )
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = tuple(pool.map(request, range(8)))

    admitted = [
        item
        for item in decisions
        if item.status is CredentialLeaseDecisionStatus.ADMITTED
    ]
    denied = [
        item
        for item in decisions
        if item.status is CredentialLeaseDecisionStatus.DENIED
    ]
    assert len(admitted) == 1
    assert len(denied) == 7
    assert {item.reason_code for item in denied} == {"policy_headroom_exceeded"}
    assert broker.active_lease_count(reference.identity) == 1


def test_child_scope_and_lifetime_cannot_exceed_parent() -> None:
    clock = _Clock()
    broker, reference = _broker(
        clock,
        resources=("octo/example", "octo/other"),
        max_active_leases=4,
    )
    parent = broker.request_lease(_request(reference, clock, request_id="parent"))
    assert parent.lease is not None

    child = broker.request_lease(
        _request(
            reference,
            clock,
            request_id="child",
            parent_lease_id=parent.lease.lease_id,
            lifetime_seconds=15,
        )
    )
    broader_resource = broker.request_lease(
        _request(
            reference,
            clock,
            request_id="broader-resource",
            parent_lease_id=parent.lease.lease_id,
            resource="octo/other",
            lifetime_seconds=15,
        )
    )
    broader_lifetime = broker.request_lease(
        _request(
            reference,
            clock,
            request_id="broader-lifetime",
            parent_lease_id=parent.lease.lease_id,
            lifetime_seconds=45,
        )
    )

    assert child.status is CredentialLeaseDecisionStatus.ADMITTED
    assert broader_resource.status is CredentialLeaseDecisionStatus.DENIED
    assert broader_resource.reason_code == "parent_scope_exceeded"
    assert broader_lifetime.status is CredentialLeaseDecisionStatus.DENIED
    assert broader_lifetime.reason_code == "parent_lifetime_exceeded"


def test_expiry_and_run_cancel_terminalize_each_lease_exactly_once() -> None:
    clock = _Clock()
    broker, reference = _broker(clock, max_active_leases=4)
    expiring = broker.request_lease(
        _request(
            reference,
            clock,
            request_id="expiring",
            lifetime_seconds=5,
        )
    )
    cancelling = broker.request_lease(
        _request(reference, clock, request_id="cancelling", parent_run_id="run-2")
    )
    assert expiring.lease is not None
    assert cancelling.lease is not None

    clock.advance(6)
    expired_once = broker.expire_leases()
    expired_twice = broker.expire_leases()
    cancelled_once = broker.revoke_run(
        "run-2",
        reason_code="run_cancelled",
        requested_at=clock.now,
    )
    cancelled_twice = broker.revoke_run(
        "run-2",
        reason_code="run_cancelled",
        requested_at=clock.now,
    )

    assert len(expired_once) == 1
    assert expired_once[0].reason_code == "lease_expired"
    assert expired_twice == ()
    assert len(cancelled_once) == 1
    assert cancelled_once[0].reason_code == "run_cancelled"
    assert cancelled_twice == ()
    assert broker.lease(expiring.lease.lease_id).status is CredentialLeaseStatus.EXPIRED
    assert (
        broker.lease(cancelling.lease.lease_id).status is CredentialLeaseStatus.REVOKED
    )


def test_broker_outage_denies_remote_lease_without_blocking_safe_local_work() -> None:
    clock = _Clock()
    broker, reference = _broker(clock)
    broker.set_available(False)
    local_events: list[str] = []

    decision = broker.request_lease(_request(reference, clock, request_id="remote"))
    local_events.append("local-analysis-complete")

    assert decision.status is CredentialLeaseDecisionStatus.DENIED
    assert decision.reason_code == "broker_unavailable"
    assert decision.lease is None
    assert local_events == ["local-analysis-complete"]


def test_wrong_scope_digest_and_expired_request_fail_closed() -> None:
    clock = _Clock()
    broker, reference = _broker(clock)
    wrong_digest = _request(reference, clock, request_id="wrong-digest")
    wrong_digest = replace(wrong_digest, scope_digest="f" * 64)

    wrong = broker.request_lease(wrong_digest)
    clock.advance(31)
    stale = broker.request_lease(_request(reference, _Clock(), request_id="stale"))

    assert wrong.status is CredentialLeaseDecisionStatus.DENIED
    assert wrong.reason_code == "scope_digest_mismatch"
    assert stale.status is CredentialLeaseDecisionStatus.DENIED
    assert stale.reason_code == "request_expired"


def _broker(
    clock: _Clock,
    *,
    resources: tuple[str, ...] = ("octo/example",),
    max_active_leases: int = 2,
) -> tuple[InMemoryCredentialBroker, SecretReference]:
    broker = InMemoryCredentialBroker(
        "fake-broker",
        now=lambda: clock.now,
        id_factory=_Ids(),
    )
    reference = SecretReference(SecretReferenceKind.TEST, "FAKE_PROVIDER_TOKEN")
    broker.register_source(
        CredentialSourceRegistration(
            source_id="source-main",
            broker_id="fake-broker",
            provider_identity_ref="provider-main@revision-7",
            secret_reference=reference,
            scope=CredentialSourceScope(
                audiences=("api.example.test",),
                resources=resources,
                operation_classes=("issue.write",),
            ),
            quota=CredentialQuotaMetadata(
                status=CredentialQuotaStatus.UNKNOWN,
                reason_code="provider_does_not_report",
            ),
            expires_at=clock.now + timedelta(minutes=5),
            max_lease_ttl_seconds=60,
            max_active_leases=max_active_leases,
        )
    )
    return broker, reference


def _request(
    reference: SecretReference,
    clock: _Clock,
    *,
    request_id: str,
    resource: str = "octo/example",
    parent_run_id: str = "run-1",
    parent_lease_id: str | None = None,
    lifetime_seconds: int = 30,
) -> CredentialLeaseRequestV1:
    return CredentialLeaseRequestV1(
        request_id=request_id,
        secret_ref_id=reference.identity,
        broker_id="fake-broker",
        audience="api.example.test",
        resource=resource,
        operation_class="issue.write",
        requested_at=clock.now,
        expires_at=clock.now + timedelta(seconds=lifetime_seconds),
        parent_run_id=parent_run_id,
        policy_digest=POLICY_DIGEST,
        scope_digest=credential_action_scope_digest(
            audience="api.example.test",
            resource=resource,
            operation_class="issue.write",
        ),
        parent_lease_id=parent_lease_id,
    )
