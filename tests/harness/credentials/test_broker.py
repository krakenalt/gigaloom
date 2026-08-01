"""Credential broker source and metadata projection contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from gigaloom.runtime.credentials import (
    CredentialQuotaMetadata,
    CredentialQuotaStatus,
    CredentialSourceRegistration,
    CredentialSourceScope,
    InMemoryCredentialBroker,
    credential_source_projection_to_dict,
)
from gigaloom.secrets import SecretReference, SecretReferenceKind


NOW = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


def test_source_projection_references_existing_owners_without_secret_material() -> None:
    broker = InMemoryCredentialBroker("fake-broker")
    reference = SecretReference(SecretReferenceKind.TEST, "FAKE_PROVIDER_TOKEN")
    source = CredentialSourceRegistration(
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
            status=CredentialQuotaStatus.KNOWN,
            unit="requests",
            limit=100,
            remaining=87,
            resets_at=datetime(2026, 8, 2, 9, 0, tzinfo=timezone.utc),
        ),
        expires_at=datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc),
    )

    projection = broker.register_source(source)
    payload = credential_source_projection_to_dict(projection)
    wire = json.dumps(payload, sort_keys=True)

    assert payload["provider_identity_ref"] == "provider-main@revision-7"
    assert payload["secret_ref_id"] == reference.identity
    assert payload["secret_ref_kind"] == "test"
    assert payload["scope"] == {
        "audiences": ["api.example.test"],
        "resources": ["octo/example"],
        "operation_classes": ["issue.write"],
        "scope_digest": source.scope.scope_digest,
    }
    assert payload["quota"] == {
        "status": "known",
        "unit": "requests",
        "limit": 100,
        "remaining": 87,
        "resets_at": "2026-08-02T09:00:00+00:00",
        "reason_code": None,
    }
    assert "FAKE_PROVIDER_TOKEN" not in wire
    assert "secret_value" not in wire
    assert "provider_profile" not in wire
    assert "display_name" not in wire


@pytest.mark.parametrize(
    ("quota", "status"),
    (
        (
            CredentialQuotaMetadata(
                status=CredentialQuotaStatus.KNOWN,
                unit="requests",
                limit=10,
                remaining=2,
            ),
            "known",
        ),
        (
            CredentialQuotaMetadata(
                status=CredentialQuotaStatus.UNKNOWN,
                reason_code="provider_does_not_report",
            ),
            "unknown",
        ),
        (
            CredentialQuotaMetadata(
                status=CredentialQuotaStatus.NOT_APPLICABLE,
                reason_code="unmetered_source",
            ),
            "not_applicable",
        ),
    ),
)
def test_quota_projection_preserves_explicit_knowledge_state(
    quota: CredentialQuotaMetadata,
    status: str,
) -> None:
    assert quota.to_dict()["status"] == status


def test_source_registration_rejects_malformed_secret_reference() -> None:
    with pytest.raises(ValueError, match="valid SecretReference"):
        CredentialSourceRegistration(
            source_id="source-main",
            broker_id="fake-broker",
            provider_identity_ref="provider-main@revision-7",
            secret_reference={  # type: ignore[arg-type]
                "kind": "test",
                "name": "FAKE_PROVIDER_TOKEN",
                "value": "must-not-enter-the-broker",
            },
            scope=CredentialSourceScope(
                audiences=("api.example.test",),
                resources=("octo/example",),
                operation_classes=("issue.write",),
            ),
            quota=CredentialQuotaMetadata(
                status=CredentialQuotaStatus.UNKNOWN,
                reason_code="not_observed",
            ),
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: CredentialQuotaMetadata(status=CredentialQuotaStatus.KNOWN),
        lambda: CredentialQuotaMetadata(
            status=CredentialQuotaStatus.UNKNOWN,
            unit="requests",
            reason_code="not_observed",
        ),
        lambda: CredentialQuotaMetadata(
            status=CredentialQuotaStatus.NOT_APPLICABLE,
            remaining=1,
            reason_code="not_metered",
        ),
    ),
)
def test_quota_metadata_rejects_inconsistent_state_factories(factory) -> None:
    with pytest.raises(ValueError):
        factory()
