"""Credential operator HTTP and lifecycle projection tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gigaloom.config import HarnessConfig
from gigaloom.registry import create_default_registry
from gigaloom.runtime.credentials import InMemoryCredentialBroker
from gigaloom.sessions import InMemoryHarnessSessionStore
from gigaloom.ui.app import create_app
from gigaloom.ui.routers.credentials import create_router
from gigaloom.ui.services.credentials import (
    DEMO_PARENT_RUN_ID,
    CredentialOperatorService,
)


NOW = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def broker_id(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}-{self.value}"

    def request_id(self) -> str:
        self.value += 1
        return f"credential-request-{self.value}"


def test_credential_operator_http_is_revision_bound_and_content_free(
    tmp_path,
) -> None:
    app = create_app(
        HarnessConfig(data_dir=str(tmp_path / "data")),
        registry=create_default_registry(include_entry_points=False),
        store=InMemoryHarnessSessionStore(),
    )
    client = TestClient(app)

    initial = client.get("/api/credentials")

    assert initial.status_code == 200
    snapshot = initial.json()
    assert snapshot["content_free"] is True
    assert snapshot["broker_id"] == "gigaloom-fake-broker-v1"
    assert len(snapshot["sources"]) == 1
    source = snapshot["sources"][0]
    assert source == {
        "source_id": "fake-github-demo",
        "broker_id": "gigaloom-fake-broker-v1",
        "provider_identity_ref": "fake-github-provider@demo-v1",
        "reference_kind": "test",
        "scope": {
            "audiences": ["api.example.test"],
            "resources": ["gigaloom/demo"],
            "operation_classes": ["issue.write"],
            "scope_digest": source["scope"]["scope_digest"],
        },
        "quota": {
            "status": "not_applicable",
            "unit": None,
            "limit": None,
            "remaining": None,
            "resets_at": None,
            "reason_code": "hermetic_demo",
        },
        "expires_at": None,
        "state": "current",
        "demo": True,
    }
    request = {
        "expected_revision": snapshot["revision"],
        "source_id": source["source_id"],
        "audience": source["scope"]["audiences"][0],
        "resource": source["scope"]["resources"][0],
        "operation_class": source["scope"]["operation_classes"][0],
        "parent_run_id": DEMO_PARENT_RUN_ID,
        "ttl_seconds": 60,
    }
    admitted = client.post(
        "/api/credentials/leases",
        json=request,
        headers={"X-GigaLoom-CSRF": "1"},
    )
    stale = client.post(
        "/api/credentials/leases",
        json=request,
        headers={"X-GigaLoom-CSRF": "1"},
    )

    assert admitted.status_code == stale.status_code == 200
    assert admitted.json()["state"] == "admitted"
    assert admitted.json()["snapshot"]["leases"][0]["status"] == "active"
    assert stale.json()["state"] == "stale"
    assert stale.json()["reason_code"] == "credential_snapshot_changed"
    assert len(stale.json()["snapshot"]["leases"]) == 1
    wire = json.dumps(stale.json(), sort_keys=True)
    assert "GIGALOOM_FAKE_BROKER_DEMO" not in wire
    assert "secret_ref_id" not in wire
    assert "secret_value" not in wire


def test_credential_operator_projects_expiry_and_idempotent_revocation() -> None:
    clock = _Clock()
    ids = _Ids()
    broker = InMemoryCredentialBroker(
        "fake-broker",
        now=clock,
        id_factory=ids.broker_id,
    )
    service = CredentialOperatorService.with_fake_broker_demo(
        broker,
        now=clock,
        request_id_factory=ids.request_id,
    )
    app = FastAPI()
    app.include_router(create_router(service))
    client = TestClient(app)

    first = client.get("/api/credentials").json()
    source = first["sources"][0]
    request = _lease_request(first, source, ttl_seconds=60)
    admitted = client.post("/api/credentials/leases", json=request).json()
    lease_id = admitted["snapshot"]["leases"][0]["lease_id"]

    revoked = client.post(
        f"/api/credentials/leases/{lease_id}/revoke",
        json={
            "expected_revision": admitted["snapshot"]["revision"],
            "confirmed": True,
        },
    ).json()
    repeated = client.post(
        f"/api/credentials/leases/{lease_id}/revoke",
        json={
            "expected_revision": revoked["snapshot"]["revision"],
            "confirmed": True,
        },
    ).json()

    assert revoked["state"] == "revoked"
    assert revoked["receipt"]["content_free"] is True
    assert revoked["snapshot"]["leases"][0]["status"] == "revoked"
    assert repeated["state"] == "already_terminal"

    second_request = _lease_request(
        repeated["snapshot"],
        source,
        ttl_seconds=10,
    )
    second = client.post("/api/credentials/leases", json=second_request).json()
    clock.value += timedelta(seconds=11)
    expired = client.get("/api/credentials").json()

    assert second["state"] == "admitted"
    assert [item["status"] for item in expired["leases"]] == [
        "revoked",
        "expired",
    ]
    assert expired["revision"] != second["snapshot"]["revision"]


def _lease_request(
    snapshot: dict[str, object],
    source: dict[str, object],
    *,
    ttl_seconds: int,
) -> dict[str, object]:
    scope = source["scope"]
    assert isinstance(scope, dict)
    return {
        "expected_revision": snapshot["revision"],
        "source_id": source["source_id"],
        "audience": scope["audiences"][0],
        "resource": scope["resources"][0],
        "operation_class": scope["operation_classes"][0],
        "parent_run_id": DEMO_PARENT_RUN_ID,
        "ttl_seconds": ttl_seconds,
    }
