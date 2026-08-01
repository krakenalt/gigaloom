"""Generated managed ACP route, probe, activation, and receipt coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import shutil

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    AgentActivationStatus,
    AgentInstallPlanV1,
    AgentIntegrityPolicy,
    AgentLifecycleScriptPolicy,
    CompatibilityStatus,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
    acp_distribution_digest,
    acp_registry_entry_digest,
)
from gigaloom.contracts.agent_installation_codec import managed_agent_artifact_to_dict
from gigaloom.harnesses.agent_profiles.installations.filesystem import atomic_write_json
from gigaloom.harnesses.agent_profiles.onboarding import (
    ManagedAcpProbeReceipt,
    ManagedAcpProbeRunner,
    ManagedAgentOnboardingService,
    ManagedProbeState,
    generate_managed_agent_profile,
)
from gigaloom.harnesses.agent_profiles.models import VersionPolicyKind


NOW = datetime(2026, 8, 1, 13, 0, tzinfo=UTC)


class StaticProbe:
    def __init__(self, receipt: ManagedAcpProbeReceipt) -> None:
        self.receipt = receipt
        self.calls = []

    def probe(self, profile, artifact, *, network_isolated):  # noqa: ANN001, ANN201
        self.calls.append((profile, artifact, network_isolated))
        return self.receipt


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(
    tmp_path: Path,
    *,
    version: str = "1.0.0",
    executable_name: str = "generic-agent",
):
    snapshot_digest = _digest(f"snapshot-{version}")
    distribution_values = {
        "kind": ACPDistributionKind.BINARY,
        "platform": "darwin",
        "architecture": "aarch64",
        "source": f"https://downloads.example.test/generic-{version}.zip",
        "package_or_archive": f"generic-{version}.zip",
        "expected_integrity": _digest(f"artifact-{version}"),
        "command": f"bin/{executable_name}",
        "arguments": ("--mode", "normal"),
        "environment": (),
        "network_origins": ("https://downloads.example.test",),
    }
    distribution = ACPDistributionV1(
        **distribution_values,
        distribution_digest=acp_distribution_digest(**distribution_values),
    )
    entry_values = {
        "registry_id": "generic-agent",
        "name": "Generic managed agent",
        "version": version,
        "description": "Hermetic managed route fixture",
        "repository": None,
        "website": None,
        "authors": (),
        "license": "MIT",
        "icon_ref": None,
        "distributions": (distribution,),
    }
    entry = ACPRegistryEntryV1(
        **entry_values,
        entry_digest=acp_registry_entry_digest(**entry_values),
        snapshot_digest=snapshot_digest,
    )
    artifact_digest = distribution.expected_integrity
    assert artifact_digest is not None
    managed_root = (
        tmp_path
        / "agents/registry/generic-agent"
        / version
        / "darwin-aarch64"
        / artifact_digest
    )
    executable = managed_root / "bin" / executable_name
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)
    artifact = ManagedAgentArtifactV1(
        install_id=f"install-{version.replace('.', '-')}",
        registry_id=entry.registry_id,
        local_agent_id="generic-agent",
        version=version,
        distribution_kind=distribution.kind,
        platform=distribution.platform,
        artifact_digest=artifact_digest,
        package_integrity=artifact_digest,
        lock_digest=_digest(f"lock-{version}"),
        managed_root=str(managed_root),
        executable_relative_path=f"bin/{executable_name}",
        command=f"bin/{executable_name}",
        arguments=distribution.arguments,
        environment=(),
        installed_at=NOW,
        status=ManagedAgentStatus.STAGED,
    )
    atomic_write_json(
        managed_root / ".artifact.json",
        managed_agent_artifact_to_dict(artifact),
    )
    plan = AgentInstallPlanV1(
        plan_id=f"plan-{version.replace('.', '-')}",
        registry_id=entry.registry_id,
        entry_digest=entry.entry_digest,
        snapshot_digest=entry.snapshot_digest,
        local_agent_id=artifact.local_agent_id,
        version=version,
        platform=distribution.platform,
        architecture=distribution.architecture,
        distribution_kind=distribution.kind,
        source=distribution.source,
        package_or_archive=distribution.package_or_archive,
        expected_integrity=distribution.expected_integrity,
        command=distribution.command,
        arguments=distribution.arguments,
        environment=distribution.environment,
        network_origins=distribution.network_origins,
        staging_root=str(tmp_path / "agents/staging" / f"plan-{version}"),
        managed_root=str(managed_root),
        integrity_policy=AgentIntegrityPolicy.REQUIRE_VERIFIED,
        lifecycle_script_policy=AgentLifecycleScriptPolicy.DISABLED,
        side_effects=("managed_artifact_creation",),
        confirmation_required=True,
        expires_at=NOW + timedelta(minutes=10),
    )
    return plan, entry, artifact


def _probe(
    *,
    state: ManagedProbeState = ManagedProbeState.READY,
    protocol_state: str = "conformant",
    protocol_version: str | None = "1",
    auth_methods: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (
        "cancellation",
        "session_new",
        "structured_prompt",
    ),
    losses: tuple[str, ...] = (),
    warnings: tuple[str, ...] = (),
    executable_observed: bool = True,
) -> ManagedAcpProbeReceipt:
    return ManagedAcpProbeReceipt(
        state=state,
        protocol_state=protocol_state,
        protocol_version=protocol_version,
        capability_snapshot_digest=(
            _digest("capabilities") if protocol_state == "conformant" else None
        ),
        process_fingerprint=_digest("process"),
        executable_observed=executable_observed,
        handshake_digest=_digest(f"handshake-{protocol_state}"),
        auth_methods=auth_methods,
        capabilities=capabilities,
        losses=losses,
        warnings=warnings,
        native_home_isolated=True,
        network_policy="enforced_deny",
        receipt_digest=_digest(f"receipt-{state.value}-{protocol_state}"),
    )


def test_generated_profile_is_structured_only_and_negotiated(tmp_path):
    _, entry, artifact = _candidate(tmp_path)
    profile = generate_managed_agent_profile(entry, artifact)

    assert profile.agent_id == artifact.local_agent_id
    assert profile.native is None
    assert profile.aliases == ()
    assert len(profile.structured_routes) == 1
    route = profile.structured_routes[0]
    assert route.transport_kind == "acp_stdio_v1"
    assert route.version_policy.kind is VersionPolicyKind.NEGOTIATED
    assert route.command_ref is not None
    assert route.command_ref.executable_name == "generic-agent"
    assert profile.source.revision == entry.version
    assert profile.source.reviewed is False


def test_ready_probe_activates_and_retains_content_free_receipts(tmp_path):
    plan, entry, artifact = _candidate(tmp_path)
    probe = StaticProbe(_probe())
    result = ManagedAgentOnboardingService(
        str(tmp_path),
        probe,
        clock=lambda: NOW,
    ).onboard(plan, entry, artifact, network_isolated=True, bytes_received=123)

    assert result.active is True
    assert result.activation.status is AgentActivationStatus.READY
    assert result.compatibility.status is CompatibilityStatus.COMPATIBLE_UNVERIFIED
    assert result.receipt.bytes_received == 123
    assert result.receipt.probe_observation_digest == result.compatibility.probe_digest
    assert result.receipt.activation_id == result.activation.activation_id
    assert result.probe.session_created is False
    assert result.probe.prompt_sent is False
    assert probe.calls[0][2] is True
    record = tmp_path / f"agents/state/onboarding/{artifact.install_id}.json"
    assert record.is_file()
    record_text = record.read_text(encoding="utf-8")
    assert "managed_root_relative" in record_text
    assert str(tmp_path) not in record_text


def test_auth_required_candidate_activates_degraded_without_authentication(tmp_path):
    plan, entry, artifact = _candidate(tmp_path)
    result = ManagedAgentOnboardingService(
        str(tmp_path),
        StaticProbe(
            _probe(
                state=ManagedProbeState.AUTH_REQUIRED,
                auth_methods=("provider-login",),
                warnings=("authentication_required",),
            )
        ),
        clock=lambda: NOW,
    ).onboard(plan, entry, artifact, network_isolated=True)

    assert result.active is True
    assert result.activation.status is AgentActivationStatus.DEGRADED
    assert "authentication_not_completed" in result.receipt.omissions
    assert result.probe.auth_methods == ("provider-login",)


def test_incompatible_update_remains_inactive_and_preserves_older_pointer(tmp_path):
    first_plan, first_entry, first_artifact = _candidate(tmp_path, version="1.0.0")
    service = ManagedAgentOnboardingService(
        str(tmp_path),
        StaticProbe(_probe()),
        clock=lambda: NOW,
    )
    first = service.onboard(
        first_plan,
        first_entry,
        first_artifact,
        network_isolated=True,
    )
    second_plan, second_entry, second_artifact = _candidate(
        tmp_path,
        version="2.0.0",
    )
    incompatible = ManagedAgentOnboardingService(
        str(tmp_path),
        StaticProbe(
            _probe(
                state=ManagedProbeState.INCOMPATIBLE,
                protocol_state="major_mismatch",
                protocol_version="2",
                capabilities=(),
                warnings=("protocol_major_mismatch",),
            )
        ),
        clock=lambda: NOW,
    ).onboard(
        second_plan,
        second_entry,
        second_artifact,
        network_isolated=True,
    )

    assert first.active is True
    assert incompatible.active is False
    assert incompatible.activation.status is AgentActivationStatus.INACTIVE
    assert incompatible.compatibility.status is CompatibilityStatus.INCOMPATIBLE
    assert incompatible.activation.previous_install_id == first_artifact.install_id
    from gigaloom.harnesses.agent_profiles.installations import (
        ManagedAgentActivationStore,
    )

    current = ManagedAgentActivationStore(tmp_path).current("generic-agent")
    assert current is not None
    assert current[0].install_id == first_artifact.install_id
    assert Path(second_artifact.managed_root).is_dir()


def test_production_probe_uses_disposable_home_and_initialize_only(tmp_path):
    plan, entry, artifact = _candidate(tmp_path, executable_name="fake-agent")
    del plan
    fixture = Path(__file__).parents[2] / "fixtures/acp/fake_agent.py"
    executable = Path(artifact.managed_root) / artifact.executable_relative_path
    shutil.copyfile(fixture, executable)
    executable.chmod(0o700)
    profile = generate_managed_agent_profile(entry, artifact)

    receipt = ManagedAcpProbeRunner().probe(
        profile,
        artifact,
        network_isolated=True,
    )

    assert receipt.protocol_state == "conformant"
    assert receipt.protocol_version == "1"
    assert receipt.executable_observed is True
    assert receipt.native_home_isolated is True
    assert receipt.network_policy == "enforced_deny"
    assert receipt.session_created is False
    assert receipt.prompt_sent is False
