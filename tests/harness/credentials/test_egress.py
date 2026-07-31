"""Hermetic last-mile credential injection boundary tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
import os

import pytest

from gigaloom.contracts.credentials import (
    CredentialLeaseDecisionStatus,
    CredentialLeaseRequestV1,
    CredentialLeaseStatus,
)
from gigaloom.contracts.credentials_codec import credential_lease_to_dict
from gigaloom.runtime.credentials import (
    CredentialEgressDenied,
    CredentialQuotaMetadata,
    CredentialQuotaStatus,
    CredentialSourceRegistration,
    CredentialSourceScope,
    CredentialTransportCancelled,
    CredentialTransportResult,
    GitHubLikeCredentialRequest,
    HermeticCredentialEgress,
    InMemoryCredentialBroker,
    credential_action_scope_digest,
    credential_source_projection_to_dict,
)
from gigaloom.secrets import (
    MemorySecretResolver,
    ResolvedSecret,
    SecretReference,
    SecretReferenceKind,
)


NOW = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
POLICY_DIGEST = "a" * 64
PAYLOAD_DIGEST = "b" * 64
RESPONSE_DIGEST = "c" * 64
SECRET_CANARY = "secret-canary-must-remain-last-mile"


class _Resolver:
    def __init__(self) -> None:
        self.backend = MemorySecretResolver({"FAKE_PROVIDER_TOKEN": SECRET_CANARY})
        self.resolve_calls = 0

    def supports(self, kind: SecretReferenceKind) -> bool:
        return self.backend.supports(kind)

    def resolve(self, reference: SecretReference, *, owner: str) -> ResolvedSecret:
        self.resolve_calls += 1
        return self.backend.resolve(reference, owner=owner)


class _Transport:
    def __init__(self) -> None:
        self.calls = 0
        self.credential_matched = False

    def dispatch(
        self,
        request: GitHubLikeCredentialRequest,
        *,
        credential: ResolvedSecret,
        reveal_owner: str,
    ) -> CredentialTransportResult:
        self.calls += 1
        self.credential_matched = credential.reveal_for(reveal_owner) == SECRET_CANARY
        assert request.payload_sha256 == PAYLOAD_DIGEST
        return CredentialTransportResult(201, RESPONSE_DIGEST)


class _CancellingTransport:
    def __init__(self) -> None:
        self.calls = 0

    def dispatch(
        self,
        request: GitHubLikeCredentialRequest,
        *,
        credential: ResolvedSecret,
        reveal_owner: str,
    ) -> CredentialTransportResult:
        self.calls += 1
        assert credential.reveal_for(reveal_owner) == SECRET_CANARY
        raise CredentialTransportCancelled("unsafe transport detail")


class _LeakingFailureTransport:
    def dispatch(
        self,
        request: GitHubLikeCredentialRequest,
        *,
        credential: ResolvedSecret,
        reveal_owner: str,
    ) -> CredentialTransportResult:
        del request
        secret = credential.reveal_for(reveal_owner)
        raise RuntimeError(f"transport failed with {secret}")


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        (
            {"url": "https://other.example.test/repos/octo/example/issues"},
            "audience_mismatch",
        ),
        ({"method": "GET"}, "method_mismatch"),
        ({"repository": "octo/other"}, "resource_mismatch"),
        ({"operation_class": "comment.write"}, "operation_mismatch"),
        ({"redirect_url": "https://api.example.test/other"}, "redirect_ambiguous"),
    ),
)
def test_wrong_destination_or_action_fails_before_secret_resolution(
    changes: dict[str, object],
    reason: str,
) -> None:
    broker, lease = _broker_and_lease()
    resolver = _Resolver()
    transport = _Transport()
    egress = HermeticCredentialEgress(
        broker,
        resolver,
        transport,
        now=lambda: NOW,
    )
    request = replace(_request(lease), **changes)

    with pytest.raises(CredentialEgressDenied, match=reason) as caught:
        egress.dispatch(request)

    assert caught.value.receipt.status.value == "denied"
    assert resolver.resolve_calls == 0
    assert transport.calls == 0


def test_valid_operation_injects_only_inside_last_mile_transport() -> None:
    broker, lease = _broker_and_lease()
    resolver = _Resolver()
    transport = _Transport()
    egress = HermeticCredentialEgress(
        broker,
        resolver,
        transport,
        now=lambda: NOW,
        id_factory=lambda prefix: f"{prefix}-fixed",
    )

    result = egress.dispatch(_request(lease))

    assert result.receipt.status.value == "injected"
    assert result.receipt.reason_code == "injected"
    assert result.transport.status_code == 201
    assert resolver.resolve_calls == 1
    assert transport.calls == 1
    assert transport.credential_matched is True


def test_cancellation_revokes_before_any_retry() -> None:
    broker, lease = _broker_and_lease()
    resolver = _Resolver()
    cancelling = _CancellingTransport()
    first = HermeticCredentialEgress(
        broker,
        resolver,
        cancelling,
        now=lambda: NOW,
    )

    with pytest.raises(CredentialEgressDenied, match="operation_cancelled"):
        first.dispatch(_request(lease))

    assert broker.lease(lease.lease_id).status is CredentialLeaseStatus.REVOKED
    retry_transport = _Transport()
    retry = HermeticCredentialEgress(
        broker,
        resolver,
        retry_transport,
        now=lambda: NOW,
    )
    with pytest.raises(CredentialEgressDenied, match="lease_binding_changed"):
        retry.dispatch(_request(lease))
    assert retry_transport.calls == 0


def test_broker_outage_revokes_an_existing_lease_before_remote_dispatch() -> None:
    broker, lease = _broker_and_lease()
    broker.set_available(False)
    resolver = _Resolver()
    transport = _Transport()
    egress = HermeticCredentialEgress(
        broker,
        resolver,
        transport,
        now=lambda: NOW,
    )

    with pytest.raises(CredentialEgressDenied, match="broker_unavailable"):
        egress.dispatch(_request(lease))

    assert broker.lease(lease.lease_id).status is CredentialLeaseStatus.REVOKED
    assert resolver.resolve_calls == 0
    assert transport.calls == 0


def test_secret_is_absent_from_every_public_and_persistable_surface(
    caplog: pytest.LogCaptureFixture,
) -> None:
    broker, lease = _broker_and_lease()
    resolver = _Resolver()
    success = HermeticCredentialEgress(
        broker,
        resolver,
        _Transport(),
        now=lambda: NOW,
    )
    request = _request(lease)
    result = success.dispatch(request)
    projection = credential_source_projection_to_dict(
        broker.list_source_projections()[0]
    )
    process_environment = {
        "PATH": "/usr/bin",
        "GIGALOOM_RUN_ID": lease.parent_run_id,
    }
    surfaces = {
        "model_request": {"credential_binding": request.to_dict()},
        "prompt": {"operation": request.operation_class},
        "transcript": {"receipt": result.to_dict()},
        "session_store": credential_lease_to_dict(lease),
        "runtime_payload": request.to_dict(),
        "web_projection": projection,
        "capsule": {"credential_result": result.to_dict()},
        "process_environment": process_environment,
    }

    caplog.set_level(logging.INFO)
    logging.getLogger(__name__).info("credential_result=%s", result.to_dict())
    serialized = json.dumps(surfaces, sort_keys=True)

    assert SECRET_CANARY not in serialized
    assert SECRET_CANARY not in caplog.text
    assert "FAKE_PROVIDER_TOKEN" not in serialized
    assert "FAKE_PROVIDER_TOKEN" not in os.environ

    failing = HermeticCredentialEgress(
        broker,
        resolver,
        _LeakingFailureTransport(),
        now=lambda: NOW,
    )
    with pytest.raises(
        CredentialEgressDenied, match="egress_transport_failed"
    ) as error:
        failing.dispatch(request)
    assert SECRET_CANARY not in str(error.value)
    assert SECRET_CANARY not in repr(error.value.receipt)


def _broker_and_lease():
    broker = InMemoryCredentialBroker("fake-broker", now=lambda: NOW)
    reference = SecretReference(SecretReferenceKind.TEST, "FAKE_PROVIDER_TOKEN")
    broker.register_source(
        CredentialSourceRegistration(
            source_id="source-main",
            broker_id="fake-broker",
            provider_identity_ref="provider-main@revision-7",
            secret_reference=reference,
            scope=CredentialSourceScope(
                audiences=("api.example.test",),
                resources=("octo/example",),
                operation_classes=("issue.write",),
            ),
            quota=CredentialQuotaMetadata(
                status=CredentialQuotaStatus.NOT_APPLICABLE,
                reason_code="unmetered_source",
            ),
            expires_at=NOW + timedelta(minutes=5),
            max_active_leases=4,
        )
    )
    decision = broker.request_lease(
        CredentialLeaseRequestV1(
            request_id="request-main",
            secret_ref_id=reference.identity,
            broker_id="fake-broker",
            audience="api.example.test",
            resource="octo/example",
            operation_class="issue.write",
            requested_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            parent_run_id="run-main",
            policy_digest=POLICY_DIGEST,
            scope_digest=credential_action_scope_digest(
                audience="api.example.test",
                resource="octo/example",
                operation_class="issue.write",
            ),
        )
    )
    assert decision.status is CredentialLeaseDecisionStatus.ADMITTED
    assert decision.lease is not None
    return broker, decision.lease


def _request(lease):
    return GitHubLikeCredentialRequest(
        lease=lease,
        url="https://api.example.test/repos/octo/example/issues",
        method="POST",
        repository="octo/example",
        operation_class="issue.write",
        policy_digest=POLICY_DIGEST,
        payload_bytes=128,
        payload_sha256=PAYLOAD_DIGEST,
    )
