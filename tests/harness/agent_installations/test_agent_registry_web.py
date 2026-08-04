"""Hermetic backend, operation, and HTTP contracts for the Web marketplace."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from gigaloom.harnesses.agent_profiles.installations import (
    AgentInstallError,
    AgentRuntimeService,
    BinaryDownloadResponse,
    LocalAgentInstallCoordinator,
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAcpProbeReceipt,
    ManagedAcpProviderBridgeProjection,
    ManagedProbeState,
)
from gigaloom.harnesses.agent_profiles.registry import decode_registry_document
from gigaloom.ui.routers.agent_installations import create_router as installation_router
from gigaloom.ui.routers.agent_registry import create_router as registry_router
from gigaloom.ui.services.agent_installations import AgentInstallationWebService
from gigaloom.ui.services.agent_registry import (
    AgentRegistryWebService,
    LocalManifestWebProjection,
)


NOW = datetime(2026, 8, 1, 16, 0, tzinfo=UTC)


class MutableRegistry:
    def __init__(self, catalog) -> None:  # noqa: ANN001
        self.value = catalog
        self.refreshes = 0

    def catalog(self, *, refresh: bool = False):  # noqa: ANN201
        if refresh:
            self.refreshes += 1
        return self.value


class MappingTransport:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.requests = []

    def fetch(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        payload = self.payloads[request.url]
        return BinaryDownloadResponse(
            status_code=200,
            final_url=request.url,
            content_length=len(payload),
            chunks=(payload,),
        )


class ReadyProbe:
    def probe(self, profile, artifact, *, network_isolated):  # noqa: ANN001, ANN201, ARG002
        assert network_isolated is True
        return ManagedAcpProbeReceipt(
            state=ManagedProbeState.READY,
            protocol_state="conformant",
            protocol_version="1",
            capability_snapshot_digest=_digest("capabilities"),
            process_fingerprint=artifact.artifact_digest,
            executable_observed=True,
            handshake_digest=_digest("handshake"),
            auth_methods=("provider-login",),
            capabilities=("cancellation", "session_new", "structured_prompt"),
            losses=(),
            warnings=("authentication_required",),
            native_home_isolated=True,
            network_policy="enforced_deny",
            receipt_digest=_digest("probe-receipt"),
            provider_bridge=ManagedAcpProviderBridgeProjection(
                status="ready",
                strategy="openai_env",
                protocols=("openai_chat_completions",),
                provider_ids=(),
                adapter_id="codex-acp",
                adapter_revision="codex-acp-v1",
                model_selection="config_override",
                reason_ids=(),
            ),
        )


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _archive(command: str) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(command, "#!/bin/sh\nexit 0\n")
    return stream.getvalue()


def _catalog(version: str = "1.0.0"):
    command = "bin/marketplace-agent"
    archive = _archive(command)
    url = f"https://downloads.example.test/marketplace-{version}.zip"
    document = json.dumps(
        {
            "version": "1.0.0",
            "agents": [
                {
                    "id": "marketplace-agent",
                    "name": "Marketplace Agent",
                    "version": version,
                    "description": "Generic Web marketplace fixture",
                    "license": "MIT",
                    "repository": "https://example.test/marketplace",
                    "distribution": {
                        "binary": {
                            "darwin-aarch64": {
                                "archive": url,
                                "sha256": hashlib.sha256(archive).hexdigest(),
                                "cmd": command,
                                "args": ["--acp"],
                            }
                        }
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    return decode_registry_document(document, fetched_at=NOW), url, archive


def _services(
    root: Path,
    *,
    submit=None,  # noqa: ANN001
    network_isolation_admitted: bool = True,
):
    catalog, url, archive = _catalog()
    registry = MutableRegistry(catalog)
    transport = MappingTransport({url: archive})
    runtime = AgentRuntimeService(
        root,
        registry,
        LocalAgentInstallCoordinator(
            root,
            platform="darwin",
            architecture="aarch64",
            probe=ReadyProbe(),
            network_isolation_admitted=network_isolation_admitted,
            binary_transport=transport,
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )

    def manifests() -> tuple[LocalManifestWebProjection, ...]:
        return (
            LocalManifestWebProjection(
                agent_id="local-reviewer",
                display_name="Local Reviewer",
                profile_digest="3" * 64,
                source="agent.toml",
                structured_route_ids=("review",),
                native_available=False,
            ),
        )

    inventory = AgentRegistryWebService(runtime, local_manifests=manifests)
    operations = AgentInstallationWebService(
        root,
        runtime,
        clock=lambda: NOW,
        submit=submit,
    )
    return runtime, registry, transport, inventory, operations


def test_inventory_is_revision_bound_filtered_and_explicitly_refreshed(tmp_path):
    _, registry, _, inventory, _ = _services(tmp_path)

    initial = inventory.inventory()
    filtered = inventory.inventory(
        "market",
        platform="darwin-aarch64",
        distribution="binary",
        integrity="verified",
        license_name="MIT",
    )
    missing = inventory.inventory(distribution="uvx")
    refreshed = inventory.inventory(refresh=True)

    assert initial.snapshot_digest == filtered.snapshot_digest
    assert [item.registry_id for item in filtered.registry_entries] == [
        "marketplace-agent"
    ]
    assert missing.registry_entries == ()
    assert initial.local_manifests[0].agent_id == "local-reviewer"
    assert refreshed.explicitly_refreshed is True and registry.refreshes == 1
    assert initial.installed == ()


def test_background_operation_emits_content_free_progress_and_deep_link(tmp_path):
    _, _, transport, inventory, operations = _services(
        tmp_path,
        submit=lambda callback: callback(),
    )

    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None
    operation = operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )

    assert preview.plan.distribution_kind.value == "binary"
    assert operation.status == "completed" and operation.terminal is True
    assert [item.state for item in operation.events] == [
        "queued",
        "resolving",
        "planned",
        "installing",
        "probing",
        "activated",
        "completed",
    ]
    assert len(transport.requests) == 1
    assert inventory.inventory().installed[0].auth_required is True
    assert inventory.inventory().installed[0].readiness.status == "ready"
    local_agent_id, href = operations.use_in_new_run("marketplace-agent")
    assert (local_agent_id, href) == (
        "marketplace-agent",
        "/web/work?agent=marketplace-agent",
    )
    state = next((tmp_path / "agent_profiles/web_operations").glob("*.json"))
    assert str(tmp_path).encode() not in state.read_bytes()
    assert b'"content_free":true' in state.read_bytes()

    replay = operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )
    assert replay.status == "failed" and len(transport.requests) == 1


def test_confirmed_install_is_bound_to_the_reviewed_registry_revision(tmp_path):
    runtime, registry, transport, _, operations = _services(
        tmp_path,
        submit=lambda callback: callback(),
    )
    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None
    registry.value = _catalog("2.0.0")[0]

    rejected = operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )

    assert rejected.status == "failed"
    assert transport.requests == [] and runtime.list() == ()


def test_web_mutations_require_explicit_confirmation(tmp_path):
    _, _, transport, _, operations = _services(tmp_path)
    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None

    with pytest.raises(ValueError, match="confirmation"):
        operations.start_install(
            "marketplace-agent",
            local_agent_id=None,
            expected_plan_id=preview.plan.plan_id,
            confirmed=False,
            allow_unverified=False,
        )
    with pytest.raises(ValueError, match="confirmation"):
        operations.start_update(
            "marketplace-agent",
            confirmed=False,
            allow_unverified=False,
        )
    with pytest.raises(ValueError, match="confirmation"):
        operations.activate("marketplace-agent", None, confirmed=False)
    with pytest.raises(ValueError, match="confirmation"):
        operations.rollback("marketplace-agent", confirmed=False)
    with pytest.raises(ValueError, match="confirmation"):
        operations.remove("marketplace-agent", confirmed=False)

    assert transport.requests == []


def test_web_install_rejects_missing_isolation_before_creating_operation(tmp_path):
    _, _, transport, _, operations = _services(
        tmp_path,
        network_isolation_admitted=False,
    )
    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None

    with pytest.raises(
        AgentInstallError,
        match="managed_agent_network_isolation_required",
    ):
        operations.start_install(
            "marketplace-agent",
            local_agent_id=None,
            expected_plan_id=preview.plan.plan_id,
            confirmed=True,
            allow_unverified=False,
        )

    assert transport.requests == []
    assert not (tmp_path / "agent_profiles/web_operations").exists()


def test_agent_runtime_services_expand_tilde_data_root(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    _, _, _, _, operations = _services(
        Path("~/.gigaloom"),
        submit=lambda callback: callback(),
    )
    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None
    operation = operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )

    assert operation.status == "completed"
    assert (
        tmp_path
        / ".gigaloom/agent_profiles/web_operations"
        / f"{operation.operation_id}.json"
    ).is_file()


def test_cancel_before_execution_and_restart_recovery_are_explicit(tmp_path):
    queued: list = []
    runtime, _, transport, _, operations = _services(
        tmp_path,
        submit=queued.append,
    )
    pending_preview = operations.preview("marketplace-agent")
    assert pending_preview.plan is not None
    pending = operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=pending_preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )
    cancellation = operations.cancel_operation(pending.operation_id)
    queued.pop()()

    assert cancellation.status == "cancel_requested"
    assert operations.inspect_operation(pending.operation_id).status == "canceled"
    assert transport.requests == []

    second_root = tmp_path / "restart"
    queued_restart: list = []
    restart_runtime, _, _, _, first_process = _services(
        second_root,
        submit=queued_restart.append,
    )
    interrupted_preview = first_process.preview("marketplace-agent")
    assert interrupted_preview.plan is not None
    interrupted = first_process.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=interrupted_preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )
    restarted = AgentInstallationWebService(
        second_root,
        restart_runtime,
        clock=lambda: NOW,
        submit=queued_restart.append,
    )
    assert restarted.inspect_operation(interrupted.operation_id).status == (
        "recovery_required"
    )
    recovery = restarted.recover()
    assert recovery.recovered_operation_ids == (interrupted.operation_id,)
    assert restarted.inspect_operation(interrupted.operation_id).status == "recovered"


def test_persisted_operation_state_rejects_content_and_exhausted_recovery(tmp_path):
    queued: list = []
    runtime, _, _, _, operations = _services(tmp_path, submit=queued.append)
    preview = operations.preview("marketplace-agent")
    assert preview.plan is not None
    operations.start_install(
        "marketplace-agent",
        local_agent_id=None,
        expected_plan_id=preview.plan.plan_id,
        confirmed=True,
        allow_unverified=False,
    )
    state = next((tmp_path / "agent_profiles/web_operations").glob("*.json"))
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["operation_id"] = "agent-op-../../escape"
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="operation id"):
        AgentInstallationWebService(tmp_path, runtime, clock=lambda: NOW)

    payload["operation_id"] = state.stem
    payload["events"][0]["reason_code"] = str(tmp_path / "operator-content")
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="event reason"):
        AgentInstallationWebService(tmp_path, runtime, clock=lambda: NOW)

    payload["status"] = "installing"
    payload["events"] = [
        {
            "sequence": sequence,
            "state": "installing",
            "reason_code": "install_started",
            "observed_at": NOW.isoformat(),
        }
        for sequence in range(64)
    ]
    state.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="status"):
        AgentInstallationWebService(tmp_path, runtime, clock=lambda: NOW)


def test_bounded_http_routers_expose_preview_operation_and_sse(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    runtime, _, _, inventory, operations = _services(
        tmp_path,
        submit=lambda callback: callback(),
    )
    app = FastAPI()
    app.include_router(registry_router(inventory))
    app.include_router(installation_router(operations))
    client = TestClient(app)

    inventory_response = client.get("/api/agent-runtimes/inventory")
    preview_response = client.post(
        "/api/agent-runtimes/installations/preview",
        json={"registry_query": "marketplace-agent"},
        headers={"X-GigaLoom-CSRF": "1"},
    )
    install_response = client.post(
        "/api/agent-runtimes/installations",
        json={
            "registry_query": "marketplace-agent",
            "local_agent_id": None,
            "expected_plan_id": preview_response.json()["plan"]["plan_id"],
            "allow_unverified": False,
            "confirmed": True,
        },
        headers={"X-GigaLoom-CSRF": "1"},
    )

    assert inventory_response.status_code == 200
    assert inventory_response.json()["install_decisions_browser_owned"] is False
    assert preview_response.status_code == 200
    assert preview_response.json()["browser_selected_distribution"] is False
    assert preview_response.json()["installation_started"] is False
    assert install_response.status_code == 200
    operation = install_response.json()
    assert operation["status"] == "completed" and operation["content_free"] is True
    install_id = operation["result_install_id"]
    assert ManagedAgentActivationStore(tmp_path).deactivate("marketplace-agent")
    inactive_use = client.get("/api/agent-runtimes/marketplace-agent/use")
    assert inactive_use.status_code == 404
    activation_response = client.post(
        "/api/agent-runtimes/marketplace-agent/activate",
        json={"install_id": install_id, "confirmed": True},
        headers={"X-GigaLoom-CSRF": "1"},
    )
    assert activation_response.status_code == 200
    activation = activation_response.json()
    assert activation["install_id"] == install_id
    assert activation["active"] is True and activation["atomic"] is True
    assert activation["probe"]["content_free"] is True
    assert activation["probe"]["readiness"]["status"] == "ready"
    refreshed_inventory = client.get("/api/agent-runtimes/inventory").json()
    assert refreshed_inventory["installed"][0]["readiness"] == {
        "schema_version": 1,
        "status": "ready",
        "acp_transport": "ready",
        "provider_bridge": "ready",
        "protocols": ["openai_chat_completions"],
        "gateway_availability": "available",
        "native_launch_available": True,
        "reason_ids": [],
        "action": "select_gateway_route",
    }
    assert runtime.inspect("marketplace-agent").active is True
    with client.stream(
        "GET",
        f"/api/agent-runtimes/installations/{operation['operation_id']}/events",
    ) as stream:
        body = "".join(stream.iter_text())
    assert stream.status_code == 200
    assert "event: progress" in body and "data:" in body

    secret_marker = str(tmp_path / "operator-secret")

    def fail_preview(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError(secret_marker)

    monkeypatch.setattr(operations, "preview", fail_preview)
    failed = client.post(
        "/api/agent-runtimes/installations/preview",
        json={"registry_query": "marketplace-agent"},
        headers={"X-GigaLoom-CSRF": "1"},
    )
    assert failed.status_code == 409
    assert secret_marker not in failed.text


def test_http_install_returns_content_free_isolation_rejection(tmp_path):
    _, _, transport, _, operations = _services(
        tmp_path,
        network_isolation_admitted=False,
    )
    app = FastAPI()
    app.include_router(installation_router(operations))
    client = TestClient(app)
    preview = client.post(
        "/api/agent-runtimes/installations/preview",
        json={"registry_query": "marketplace-agent"},
        headers={"X-GigaLoom-CSRF": "1"},
    ).json()

    response = client.post(
        "/api/agent-runtimes/installations",
        json={
            "registry_query": "marketplace-agent",
            "local_agent_id": None,
            "expected_plan_id": preview["plan"]["plan_id"],
            "allow_unverified": False,
            "confirmed": True,
        },
        headers={"X-GigaLoom-CSRF": "1"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "managed_agent_network_isolation_required"}
    assert transport.requests == []
