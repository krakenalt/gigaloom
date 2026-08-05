"""Shared CLI/Web runtime lifecycle, byte-stable lock, and alias coverage."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from gigaloom.cli_commands.commands import agent_runtimes as runtime_commands
from gigaloom.cli_commands.handlers import agent_runtimes as runtime_handlers
from gigaloom.config import HarnessConfig
from gigaloom.contracts import (
    AgentInstallPlanV1,
    AgentIntegrityPolicy,
    AgentLifecycleScriptPolicy,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
)
from gigaloom.contracts.agent_installation_codec import managed_agent_artifact_to_dict
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentInstallError,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
    BinaryDownloadResponse,
    LocalAgentInstallCoordinator,
    ManagedAgentActivationStore,
    AgentRuntimeService,
    InstallPlanningResult,
    project_agent_runtime_readiness,
    read_agent_lock_file,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import atomic_write_json
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAcpProbeReceipt,
    ManagedAcpProviderBridgeProjection,
    ManagedAgentOnboardingService,
    ManagedProbeState,
)
from gigaloom.harnesses.agent_profiles.registry import decode_registry_document


NOW = datetime(2026, 8, 1, 14, 0, tzinfo=UTC)


class StaticRegistry:
    def __init__(self, catalog) -> None:  # noqa: ANN001
        self.value = catalog
        self.refreshes = 0

    def catalog(self, *, refresh: bool = False):  # noqa: ANN201
        if refresh:
            self.refreshes += 1
        return self.value


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
            auth_methods=(),
            capabilities=("cancellation", "session_new", "structured_prompt"),
            losses=(),
            warnings=(),
            native_home_isolated=True,
            network_policy="enforced_deny",
            receipt_digest=_digest("probe-receipt"),
        )


class FakeCoordinator:
    """Backend-owned fake that persists real onboarding records in temp roots."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.install_calls = 0
        self.preview_calls = 0
        self.probe_calls = 0
        self.probe_result: ManagedAcpProbeReceipt | None = None
        self.sync_calls = 0

    def preview(
        self,
        entry,
        catalog,
        *,
        inventory,
        local_agent_id,
    ) -> InstallPlanningResult:  # noqa: ANN001
        self.preview_calls += 1
        return AgentInstallPlanner(
            AgentInstallPlannerPolicy(
                platform="darwin",
                architecture="aarch64",
                data_root=str(self.root),
            )
        ).plan(
            entry,
            catalog.snapshot,
            inventory=inventory,
            local_agent_id=local_agent_id,
            now=NOW,
        )

    def install(
        self,
        entry,
        catalog,
        *,
        inventory,
        local_agent_id,
        confirmed,
        allow_unverified,
        expected_plan_id=None,
        cancellation=None,
        progress=None,
    ):  # noqa: ANN001, ANN201
        del inventory, allow_unverified, expected_plan_id, cancellation, progress
        assert confirmed is True
        self.install_calls += 1
        return _install_entry(
            self.root,
            entry,
            catalog,
            local_agent_id=local_agent_id or entry.registry_id,
        )

    def probe(self, record):  # noqa: ANN001, ANN201
        self.probe_calls += 1
        return self.probe_result or record.probe

    def sync(
        self,
        lock,
        entry,
        catalog,
        *,
        inventory,
        confirmed,
    ):  # noqa: ANN001, ANN201
        self.sync_calls += 1
        return self.install(
            entry,
            catalog,
            inventory=inventory,
            local_agent_id=lock.local_agent_id,
            confirmed=confirmed,
            allow_unverified=False,
        )

    def recover_abandoned(self):  # noqa: ANN201
        return ()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _catalog(version: str = "1.0.0"):
    payload = json.dumps(
        {
            "version": "1.0.0",
            "agents": [
                {
                    "id": "generic-runtime",
                    "name": "Generic runtime agent",
                    "version": version,
                    "description": "Runtime lifecycle fixture",
                    "license": "MIT",
                    "distribution": {
                        "binary": {
                            "darwin-aarch64": {
                                "archive": f"https://downloads.example.test/generic-{version}.zip",
                                "sha256": _digest(f"artifact-{version}"),
                                "cmd": "bin/generic-runtime",
                                "args": ["--acp"],
                            }
                        }
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    return decode_registry_document(payload, fetched_at=NOW)


def _binary_catalog(archive: bytes, *, registry_id: str = "coordinated-runtime"):
    payload = json.dumps(
        {
            "version": "1.0.0",
            "agents": [
                {
                    "id": registry_id,
                    "name": "Coordinated runtime agent",
                    "version": "1.0.0",
                    "description": "End-to-end coordinator fixture",
                    "license": "MIT",
                    "distribution": {
                        "binary": {
                            "darwin-aarch64": {
                                "archive": "https://downloads.example.test/coordinated.zip",
                                "sha256": hashlib.sha256(archive).hexdigest(),
                                "cmd": "bin/coordinated-runtime",
                                "args": ["--acp"],
                            }
                        }
                    },
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    return decode_registry_document(payload, fetched_at=NOW)


def _zip_bytes() -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("bin/coordinated-runtime", "#!/bin/sh\nexit 0\n")
    return stream.getvalue()


class MemoryTransport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests = []

    def fetch(self, request):  # noqa: ANN001, ANN201
        self.requests.append(request)
        return BinaryDownloadResponse(
            status_code=200,
            final_url=request.url,
            content_length=len(self.payload),
            chunks=(self.payload,),
        )


def _install_entry(
    root: Path,
    entry,
    catalog,
    *,
    local_agent_id: str,
):  # noqa: ANN001, ANN201
    distribution = entry.distributions[0]
    artifact_digest = distribution.expected_integrity
    assert artifact_digest is not None
    plan = AgentInstallPlanV1(
        plan_id=f"plan-{entry.version.replace('.', '-')}",
        registry_id=entry.registry_id,
        entry_digest=entry.entry_digest,
        snapshot_digest=catalog.snapshot.snapshot_digest,
        local_agent_id=local_agent_id,
        version=entry.version,
        platform="darwin",
        architecture="aarch64",
        distribution_kind=distribution.kind,
        source=distribution.source,
        package_or_archive=distribution.package_or_archive,
        expected_integrity=artifact_digest,
        command=distribution.command,
        arguments=distribution.arguments,
        environment=distribution.environment,
        network_origins=distribution.network_origins,
        staging_root=str(root / "agents/staging" / f"plan-{entry.version}"),
        managed_root=str(
            root
            / "agents/registry"
            / local_agent_id
            / entry.version
            / "darwin-aarch64"
            / artifact_digest
        ),
        integrity_policy=AgentIntegrityPolicy.REQUIRE_VERIFIED,
        lifecycle_script_policy=AgentLifecycleScriptPolicy.DISABLED,
        side_effects=("managed_artifact_creation",),
        confirmation_required=True,
        expires_at=NOW + timedelta(minutes=10),
    )
    install_id = (
        "install-"
        + canonical_digest(
            {
                "registry_id": plan.registry_id,
                "local_agent_id": plan.local_agent_id,
                "version": plan.version,
                "platform": plan.platform,
                "architecture": plan.architecture,
                "artifact_digest": artifact_digest,
            }
        )[:24]
    )
    managed_root = Path(plan.managed_root)
    executable = managed_root / distribution.command
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    artifact = ManagedAgentArtifactV1(
        install_id=install_id,
        registry_id=entry.registry_id,
        local_agent_id=local_agent_id,
        version=entry.version,
        distribution_kind=distribution.kind,
        platform="darwin",
        artifact_digest=artifact_digest,
        package_integrity=artifact_digest,
        lock_digest=_digest(f"lock-{entry.version}"),
        managed_root=str(managed_root),
        executable_relative_path=distribution.command,
        command=distribution.command,
        arguments=distribution.arguments,
        environment=distribution.environment,
        installed_at=NOW,
        status=ManagedAgentStatus.STAGED,
    )
    atomic_write_json(
        managed_root / ".artifact.json",
        managed_agent_artifact_to_dict(artifact),
    )
    return ManagedAgentOnboardingService(
        str(root),
        ReadyProbe(),
        clock=lambda: NOW,
    ).onboard(plan, entry, artifact, network_isolated=True)


def _runtime(root: Path, catalog=None):  # noqa: ANN001
    resolved = catalog or _catalog()
    coordinator = FakeCoordinator(root)
    service = AgentRuntimeService(
        root,
        StaticRegistry(resolved),
        coordinator,
        clock=lambda: NOW,
        reserved_inventory=AgentIdentityInventory(native_agent_ids=("gemini",)),
    )
    return service, coordinator


def test_search_and_dry_run_never_install_or_mutate_state(tmp_path):
    service, coordinator = _runtime(tmp_path)

    page = service.search("generic")
    preview = service.add("generic-runtime", dry_run=True)

    assert [item.registry_id for item in page.entries] == ["generic-runtime"]
    assert isinstance(preview, InstallPlanningResult) and preview.plan is not None
    assert coordinator.preview_calls == 1
    assert coordinator.install_calls == 0
    assert service.list() == ()


def test_production_coordinator_executes_one_confirmed_binary_transaction(tmp_path):
    archive = _zip_bytes()
    catalog = _binary_catalog(archive)
    transport = MemoryTransport(archive)
    source_root = tmp_path / "source"
    coordinator = LocalAgentInstallCoordinator(
        source_root,
        platform="darwin",
        architecture="aarch64",
        probe=ReadyProbe(),
        network_isolation_admitted=True,
        binary_transport=transport,
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        source_root,
        StaticRegistry(catalog),
        coordinator,
        clock=lambda: NOW,
    )

    preview = service.add("coordinated-runtime", dry_run=True)
    result = service.add("coordinated-runtime", confirmed=True)

    assert isinstance(preview, InstallPlanningResult) and preview.plan is not None
    assert not isinstance(result, InstallPlanningResult) and result.active is True
    assert len(transport.requests) == 1
    assert service.inspect("coordinated-runtime") == result
    assert Path(result.artifact.managed_root, "bin/coordinated-runtime").is_file()

    lock_path = tmp_path / "agents.lock"
    lockset = service.lock(lock_path)
    target_root = tmp_path / "target"
    target_transport = MemoryTransport(archive)
    target = AgentRuntimeService(
        target_root,
        StaticRegistry(catalog),
        LocalAgentInstallCoordinator(
            target_root,
            platform="darwin",
            architecture="aarch64",
            probe=ReadyProbe(),
            network_isolation_admitted=True,
            binary_transport=target_transport,
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )

    synced = target.sync(lock_path, confirmed=True)

    assert len(synced) == 1 and synced[0].profile.profile_digest == (
        lockset.agents[0].generated_profile_digest
    )
    assert target.sync(lock_path, confirmed=True) == ()
    assert len(target_transport.requests) == 1


def test_production_coordinator_fails_closed_without_isolation_authority(tmp_path):
    archive = _zip_bytes()
    catalog = _binary_catalog(archive)
    transport = MemoryTransport(archive)
    coordinator = LocalAgentInstallCoordinator(
        tmp_path,
        platform="darwin",
        architecture="aarch64",
        probe=ReadyProbe(),
        network_isolation_admitted=False,
        binary_transport=transport,
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        tmp_path,
        StaticRegistry(catalog),
        coordinator,
        clock=lambda: NOW,
    )

    assert isinstance(
        service.add("coordinated-runtime", dry_run=True),
        InstallPlanningResult,
    )
    with pytest.raises(
        AgentInstallError,
        match="managed_agent_network_isolation_required",
    ):
        service.add("coordinated-runtime", confirmed=True)
    assert transport.requests == []
    assert service.list() == ()


def test_registry_identity_collision_requires_explicit_alias(tmp_path):
    archive = _zip_bytes()
    catalog = _binary_catalog(archive, registry_id="gemini")
    coordinator = LocalAgentInstallCoordinator(
        tmp_path,
        platform="darwin",
        architecture="aarch64",
        probe=ReadyProbe(),
        network_isolation_admitted=True,
        binary_transport=MemoryTransport(archive),
        clock=lambda: NOW,
    )
    service = AgentRuntimeService(
        tmp_path,
        StaticRegistry(catalog),
        coordinator,
        clock=lambda: NOW,
        reserved_inventory=AgentIdentityInventory(native_agent_ids=("gemini",)),
    )

    preview = service.add("gemini", dry_run=True)

    assert isinstance(preview, InstallPlanningResult) and preview.plan is None
    assert preview.proposed_local_agent_id == "gemini-acp"
    assert preview.collision_namespaces == ("native_agent",)


def test_add_list_inspect_probe_lock_and_remove_share_one_state(tmp_path):
    service, coordinator = _runtime(tmp_path)
    result = service.add("generic-runtime", confirmed=True)
    assert not isinstance(result, InstallPlanningResult)

    summaries = service.list()
    assert len(summaries) == 1 and summaries[0].active is True
    assert (
        service.inspect("generic-runtime").artifact.install_id
        == result.artifact.install_id
    )
    assert service.probe("generic-runtime").state is ManagedProbeState.READY
    first_lock = tmp_path / "project/.giga/agents.lock"
    second_lock = tmp_path / "project/.giga/agents-copy.lock"
    lockset = service.lock(first_lock)
    service.lock(second_lock)

    assert coordinator.install_calls == 1
    assert first_lock.read_bytes() == second_lock.read_bytes()
    assert read_agent_lock_file(first_lock) == lockset
    assert str(tmp_path).encode() not in first_lock.read_bytes()
    assert service.remove("generic-runtime", confirmed=True) == 1
    assert service.list() == ()
    assert not Path(result.artifact.managed_root).exists()


def test_shared_runtime_readiness_has_four_compact_presentation_states(tmp_path):
    service, _ = _runtime(tmp_path)
    installed = service.add("generic-runtime", confirmed=True)
    assert not isinstance(installed, InstallPlanningResult)
    probe = installed.probe

    def readiness(status: str, *, active: bool = True):
        strategy = "openai_env" if status == "ready" else None
        projection = ManagedAcpProviderBridgeProjection(
            status=status,
            strategy=strategy,
            protocols=("openai_chat_completions",) if status == "ready" else (),
            provider_ids=(),
            adapter_id="codex-acp" if status == "ready" else None,
            adapter_revision="codex-acp-v1" if status == "ready" else None,
            model_selection="config_override" if status == "ready" else None,
            reason_ids=(() if status == "ready" else (f"provider_bridge_{status}",)),
        )
        return project_agent_runtime_readiness(
            replace(probe, provider_bridge=projection),
            active=active,
        )

    assert readiness("ready").status == "ready"
    assert readiness("native_only").status == "native-only"
    assert readiness("unknown_until_reprobe").status == "reprobe"
    blocked = readiness("blocked")
    assert blocked.status == "blocked" and blocked.native_launch_available is True
    inactive = readiness("ready", active=False)
    assert inactive.status == "blocked" and inactive.action == "activate"


def test_inactive_revision_reprobes_and_activates_atomically(tmp_path):
    service, coordinator = _runtime(tmp_path)
    installed = service.add("generic-runtime", confirmed=True)
    assert not isinstance(installed, InstallPlanningResult)
    assert ManagedAgentActivationStore(tmp_path).deactivate("generic-runtime") is True
    assert service.list()[0].active is False
    assert service.inspect("generic-runtime").active is False

    with pytest.raises(ValueError, match="confirmation"):
        service.activate("generic-runtime", confirmed=False)

    activated = service.activate(
        "generic-runtime",
        install_id=installed.artifact.install_id,
        confirmed=True,
    )

    assert coordinator.probe_calls == 1
    assert activated.active is True
    assert activated.activation.status.value == "ready"
    assert activated.probe.state is ManagedProbeState.READY
    assert service.inspect("generic-runtime") == activated
    assert service.list()[0].active is True
    assert next((tmp_path / "agents/state/reactivations").glob("*.json")).is_file()


def test_activation_evidence_failure_never_publishes_active_pointer(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    service, _ = _runtime(tmp_path)
    installed = service.add("generic-runtime", confirmed=True)
    assert not isinstance(installed, InstallPlanningResult)
    assert ManagedAgentActivationStore(tmp_path).deactivate("generic-runtime") is True

    def reject_evidence(result) -> None:  # noqa: ANN001
        del result
        raise OSError("simulated evidence failure")

    monkeypatch.setattr(service._reactivation._evidence, "save", reject_evidence)
    with pytest.raises(OSError, match="evidence failure"):
        service.activate("generic-runtime", confirmed=True)

    assert ManagedAgentActivationStore(tmp_path).current("generic-runtime") is None


def test_failed_activation_retains_inactive_revision_and_fresh_probe(tmp_path):
    service, coordinator = _runtime(tmp_path)
    installed = service.add("generic-runtime", confirmed=True)
    assert not isinstance(installed, InstallPlanningResult)
    assert ManagedAgentActivationStore(tmp_path).deactivate("generic-runtime") is True
    coordinator.probe_result = replace(
        installed.probe,
        state=ManagedProbeState.UNAVAILABLE,
        protocol_state="unavailable",
        protocol_version=None,
        capability_snapshot_digest=None,
        capabilities=(),
        warnings=("acp_initialize_unavailable",),
        handshake_digest=_digest("unavailable-handshake"),
        receipt_digest=_digest("unavailable-receipt"),
    )

    result = service.activate("generic-runtime", confirmed=True)

    assert result.active is False
    assert result.activation.status.value == "inactive"
    assert result.probe.state is ManagedProbeState.UNAVAILABLE
    summary = service.list()[0]
    assert summary.active is False and summary.probe_state == "unavailable"
    assert ManagedAgentActivationStore(tmp_path).current("generic-runtime") is None


def test_lock_sync_is_exact_idempotent_and_never_upgrades(tmp_path):
    source_root = tmp_path / "source"
    source, _ = _runtime(source_root)
    source.add("generic-runtime", confirmed=True)
    lock_path = tmp_path / "agents.lock"
    source.lock(lock_path)

    target_root = tmp_path / "target"
    target, coordinator = _runtime(target_root)
    installed = target.sync(lock_path, confirmed=True)
    repeated = target.sync(lock_path, confirmed=True)

    assert len(installed) == 1
    assert repeated == ()
    assert coordinator.sync_calls == 1
    assert target.inspect("generic-runtime").artifact.version == "1.0.0"

    changed, _ = _runtime(tmp_path / "changed", _catalog("2.0.0"))
    try:
        changed.sync(lock_path, confirmed=True)
    except ValueError as error:
        assert "exact registry snapshot" in str(error)
    else:
        raise AssertionError("sync silently substituted a newer registry revision")


def test_outdated_update_and_rollback_are_explicit_side_by_side(tmp_path):
    old_catalog = _catalog("1.0.0")
    service, _ = _runtime(tmp_path, old_catalog)
    service.add("generic-runtime", confirmed=True)
    new_catalog = _catalog("2.0.0")
    service._registry.value = new_catalog  # type: ignore[attr-defined]

    assert [item.version for item in service.outdated()] == ["1.0.0"]
    updated = service.update("generic-runtime", confirmed=True)
    assert updated.artifact.version == "2.0.0"
    assert service.inspect("generic-runtime").artifact.version == "2.0.0"
    rollback = service.rollback("generic-runtime")
    assert rollback.install_id != updated.artifact.install_id
    assert service.inspect("generic-runtime").artifact.version == "1.0.0"


def test_command_metadata_preserves_manifest_and_acp_aliases():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    runtime_commands.register(subparsers)

    manifest = parser.parse_args(["agent", "add", "--manifest", "agent.toml"])
    agent_search = parser.parse_args(["agent", "search", "generic"])
    alias_search = parser.parse_args(["acp", "search", "generic"])
    sync = parser.parse_args(
        ["agent", "sync", "--lock", ".giga/agents.lock", "--yes", "--json"]
    )
    activate = parser.parse_args(
        [
            "agent",
            "activate",
            "generic-runtime",
            "--install-id",
            "install-generic",
            "--yes",
            "--json",
        ]
    )

    assert manifest.manifest == "agent.toml" and manifest.registry_query is None
    assert (
        agent_search.handler == alias_search.handler == "_handle_agent_runtime_search"
    )
    assert sync.handler == "_handle_agent_runtime_sync"
    assert sync.yes is True and sync.json is True
    assert activate.handler == "_handle_agent_runtime_activate"
    assert activate.install_id == "install-generic"


def test_json_cli_search_and_add_alias_use_injected_shared_service(
    tmp_path,
    capsys,
):
    service, coordinator = _runtime(tmp_path)
    runtime_handlers.configure_agent_runtime_service_factory(lambda config: service)
    config = HarnessConfig(data_dir=tmp_path)
    try:
        search_args = argparse.Namespace(query="generic", refresh=False, json=True)
        add_args = argparse.Namespace(
            registry_query="generic-runtime",
            manifest=None,
            local_agent_id=None,
            dry_run=True,
            yes=False,
            allow_unverified=False,
            refresh=False,
            json=True,
        )
        assert runtime_handlers._handle_agent_runtime_search(search_args, config) == 0
        search_payload = json.loads(capsys.readouterr().out)
        assert search_payload["entries"][0]["registry_id"] == "generic-runtime"
        assert runtime_handlers._handle_agent_runtime_add(add_args, config) == 0
        add_payload = json.loads(capsys.readouterr().out)
        assert add_payload["plan"]["registry_id"] == "generic-runtime"
        assert coordinator.install_calls == 0

        installed = service.add("generic-runtime", confirmed=True)
        assert not isinstance(installed, InstallPlanningResult)
        list_args = argparse.Namespace(json=True)
        assert runtime_handlers._handle_agent_runtime_list(list_args, config) == 0
        list_payload = json.loads(capsys.readouterr().out)
        assert list_payload["schema_version"] == 1
        assert list_payload["installed_revisions"][0]["readiness"] == {
            "schema_version": 1,
            "status": "reprobe",
            "acp_transport": "ready",
            "provider_bridge": "reprobe",
            "protocols": [],
            "gateway_availability": "reprobe",
            "native_launch_available": True,
            "reason_ids": ["provider_bridge_reprobe_required"],
            "action": "reprobe",
        }
        inspect_args = argparse.Namespace(local_agent_id="generic-runtime", json=True)
        assert runtime_handlers._handle_agent_runtime_inspect(inspect_args, config) == 0
        inspect_payload = json.loads(capsys.readouterr().out)
        assert inspect_payload["readiness"]["status"] == "reprobe"
        assert inspect_payload["provider_bridge"] == {
            "status": "unknown_until_reprobe",
            "strategy": None,
            "protocols": [],
            "provider_ids": [],
            "adapter_id": None,
            "adapter_revision": None,
            "model_selection": None,
            "reason_ids": ["provider_bridge_reprobe_required"],
        }
        coordinator.probe_result = replace(
            installed.probe,
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
        probe_args = argparse.Namespace(local_agent_id="generic-runtime", json=True)
        assert runtime_handlers._handle_agent_runtime_probe(probe_args, config) == 0
        probe_payload = json.loads(capsys.readouterr().out)
        assert probe_payload["schema_version"] == 1
        assert probe_payload["readiness"]["status"] == "ready"
        assert probe_payload["provider_bridge"]["status"] == "ready"
        ManagedAgentActivationStore(tmp_path).deactivate("generic-runtime")
        activate_args = argparse.Namespace(
            local_agent_id="generic-runtime",
            install_id=installed.artifact.install_id,
            yes=True,
            json=True,
        )
        assert (
            runtime_handlers._handle_agent_runtime_activate(activate_args, config) == 0
        )
        activation_payload = json.loads(capsys.readouterr().out)
        assert activation_payload["active"] is True
        assert activation_payload["install_id"] == installed.artifact.install_id
    finally:
        runtime_handlers.configure_agent_runtime_service_factory(None)
