"""ACP Registry and transactional managed-install contract tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from gigaloom.contracts import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    ACPRegistrySnapshotV1,
    ACPRegistrySourceKind,
    AgentActivationStatus,
    AgentActivationV1,
    AgentCleanupStatus,
    AgentInstallPlanV1,
    AgentInstallationOutcome,
    AgentInstallationReceiptV1,
    AgentIntegrityPolicy,
    AgentLifecycleScriptPolicy,
    AgentLockV1,
    ExtractionLimitsV1,
    InstallationTransitionV1,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
    acp_distribution_digest,
    acp_distribution_from_dict,
    acp_distribution_to_dict,
    acp_registry_entry_digest,
    acp_registry_entry_from_dict,
    acp_registry_entry_to_dict,
    acp_registry_snapshot_from_dict,
    acp_registry_snapshot_to_dict,
    agent_activation_from_dict,
    agent_activation_to_dict,
    agent_install_plan_from_dict,
    agent_install_plan_to_dict,
    agent_installation_receipt_from_dict,
    agent_installation_receipt_to_dict,
    agent_lock_from_dict,
    agent_lock_to_dict,
    managed_agent_artifact_from_dict,
    managed_agent_artifact_to_dict,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _npm_integrity() -> str:
    return f"sha512-{'A' * 86}=="


def _distribution() -> ACPDistributionV1:
    values = {
        "kind": ACPDistributionKind.NPX,
        "platform": "darwin",
        "architecture": "arm64",
        "source": "https://registry.npmjs.org",
        "package_or_archive": "@example/acp-agent@1.2.3",
        "expected_integrity": _npm_integrity(),
        "command": "example-acp",
        "arguments": ("--acp",),
        "environment": (("NO_COLOR", "1"), ("ACP_MODE", "stdio")),
        "network_origins": (
            "https://registry.npmjs.org",
            "https://cdn.example.com",
        ),
    }
    return ACPDistributionV1(
        **values,
        distribution_digest=acp_distribution_digest(**values),
    )


def _entry(distribution: ACPDistributionV1 | None = None) -> ACPRegistryEntryV1:
    distributions = (distribution or _distribution(),)
    values = {
        "registry_id": "example-acp",
        "name": "Example ACP Agent",
        "version": "1.2.3",
        "description": "Hermetic registry fixture",
        "repository": "https://github.com/example/acp-agent",
        "website": "https://example.com",
        "authors": ("Example Maintainers",),
        "license": "Apache-2.0",
        "icon_ref": None,
        "distributions": distributions,
    }
    return ACPRegistryEntryV1(
        **values,
        entry_digest=acp_registry_entry_digest(**values),
        snapshot_digest=_digest("snapshot"),
    )


def test_registry_snapshot_entry_and_distribution_round_trip_exactly():
    distribution = _distribution()
    entry = _entry(distribution)
    snapshot = ACPRegistrySnapshotV1(
        source_url="https://registry.example.com/v1/index.json",
        source_kind=ACPRegistrySourceKind.OFFICIAL,
        fetched_at=NOW,
        etag="fixture-etag",
        last_modified="Sat, 01 Aug 2026 09:00:00 GMT",
        snapshot_digest=entry.snapshot_digest,
        entry_count=1,
        entries_digest=_digest("entries"),
        stale=False,
    )

    assert (
        acp_distribution_from_dict(acp_distribution_to_dict(distribution))
        == distribution
    )
    assert acp_registry_entry_from_dict(acp_registry_entry_to_dict(entry)) == entry
    assert (
        acp_registry_snapshot_from_dict(acp_registry_snapshot_to_dict(snapshot))
        == snapshot
    )
    assert distribution.environment[0][0] == "ACP_MODE"
    assert distribution.network_origins[0] == "https://cdn.example.com"


def test_registry_contract_is_inert_digest_bound_and_secret_free():
    distribution = _distribution()

    with pytest.raises(ValueError, match="digest does not match"):
        replace(distribution, command="different-command")
    with pytest.raises(ValueError, match="secret-bearing key"):
        values = {
            "kind": distribution.kind,
            "platform": distribution.platform,
            "architecture": distribution.architecture,
            "source": distribution.source,
            "package_or_archive": distribution.package_or_archive,
            "expected_integrity": distribution.expected_integrity,
            "command": distribution.command,
            "arguments": distribution.arguments,
            "environment": (("API_TOKEN", "not-allowed"),),
            "network_origins": distribution.network_origins,
        }
        ACPDistributionV1(
            **values,
            distribution_digest=acp_distribution_digest(**values),
        )
    payload = acp_registry_entry_to_dict(_entry(distribution))
    with pytest.raises(ValueError, match="unknown fields"):
        acp_registry_entry_from_dict({**payload, "install_now": True})


def _plan() -> AgentInstallPlanV1:
    distribution = _distribution()
    entry = _entry(distribution)
    return AgentInstallPlanV1(
        plan_id="plan-1",
        registry_id=entry.registry_id,
        entry_digest=entry.entry_digest,
        snapshot_digest=entry.snapshot_digest,
        local_agent_id="example-acp",
        version=entry.version,
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
        staging_root="/managed/staging/plan-1",
        managed_root="/managed/agents/example-acp/1.2.3",
        integrity_policy=AgentIntegrityPolicy.REQUIRE_VERIFIED,
        lifecycle_script_policy=AgentLifecycleScriptPolicy.DISABLED,
        side_effects=("private_package_install",),
        confirmation_required=True,
        expires_at=NOW + timedelta(minutes=10),
    )


def test_install_plan_and_managed_artifact_round_trip_without_shell_strings():
    plan = _plan()
    artifact = ManagedAgentArtifactV1(
        install_id="install-1",
        registry_id=plan.registry_id,
        local_agent_id=plan.local_agent_id,
        version=plan.version,
        distribution_kind=plan.distribution_kind,
        platform=plan.platform,
        artifact_digest=_digest("artifact"),
        package_integrity=plan.expected_integrity,
        lock_digest=_digest("package lock"),
        managed_root=plan.managed_root,
        executable_relative_path="bin/example-acp",
        command=plan.command,
        arguments=plan.arguments,
        environment=plan.environment,
        installed_at=NOW,
        status=ManagedAgentStatus.READY,
    )

    assert agent_install_plan_from_dict(agent_install_plan_to_dict(plan)) == plan
    assert (
        managed_agent_artifact_from_dict(managed_agent_artifact_to_dict(artifact))
        == artifact
    )
    assert agent_install_plan_to_dict(plan)["arguments"] == ["--acp"]

    with pytest.raises(ValueError, match="requires explicit confirmation"):
        replace(
            plan,
            expected_integrity=None,
            integrity_policy=AgentIntegrityPolicy.ALLOW_EXPLICIT_UNVERIFIED,
            confirmation_required=False,
        )


def test_activation_receipt_and_lock_bind_exact_install_evidence():
    plan = _plan()
    activation = AgentActivationV1(
        activation_id="activation-1",
        install_id="install-1",
        previous_install_id="install-previous",
        local_agent_id=plan.local_agent_id,
        profile_digest=_digest("profile"),
        compatibility_observation_digest=_digest("compatibility"),
        activated_at=NOW,
        status=AgentActivationStatus.READY,
    )
    transitions = (
        InstallationTransitionV1(
            sequence=0,
            state="started",
            timestamp=NOW,
            evidence_digest=_digest("start"),
            reason_code="transaction_started",
        ),
        InstallationTransitionV1(
            sequence=1,
            state="activated",
            timestamp=NOW + timedelta(seconds=1),
            evidence_digest=_digest("activation"),
            reason_code="activation_completed",
        ),
    )
    receipt = AgentInstallationReceiptV1(
        receipt_id="receipt-1",
        plan_id=plan.plan_id,
        install_id=activation.install_id,
        registry_id=plan.registry_id,
        entry_digest=plan.entry_digest,
        snapshot_digest=plan.snapshot_digest,
        transitions=transitions,
        bytes_received=1024,
        artifact_digest=_digest("artifact"),
        package_integrity=plan.expected_integrity,
        extraction_limits=ExtractionLimitsV1(
            max_bytes=10_000_000,
            max_files=10_000,
            max_path_depth=32,
        ),
        probe_observation_digest=activation.compatibility_observation_digest,
        activation_id=activation.activation_id,
        rollback_install_id=activation.previous_install_id,
        omissions=(),
        cleanup_status=AgentCleanupStatus.NOT_REQUIRED,
        outcome=AgentInstallationOutcome.SUCCEEDED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )
    lock = AgentLockV1(
        lock_id="lock-1",
        registry_id=plan.registry_id,
        snapshot_digest=plan.snapshot_digest,
        entry_digest=plan.entry_digest,
        local_agent_id=plan.local_agent_id,
        version=plan.version,
        platform=plan.platform,
        architecture=plan.architecture,
        distribution_kind=plan.distribution_kind,
        artifact_digest=_digest("artifact"),
        package_integrity=plan.expected_integrity,
        command=plan.command,
        arguments=plan.arguments,
        environment=plan.environment,
        generated_profile_digest=activation.profile_digest,
    )

    assert (
        agent_activation_from_dict(agent_activation_to_dict(activation)) == activation
    )
    assert (
        agent_installation_receipt_from_dict(
            agent_installation_receipt_to_dict(receipt)
        )
        == receipt
    )
    lock_payload = agent_lock_to_dict(lock)
    assert agent_lock_from_dict(lock_payload) == lock
    assert "managed_root" not in lock_payload
    assert "staging_root" not in lock_payload

    with pytest.raises(ValueError, match="digest does not match"):
        agent_lock_from_dict({**lock_payload, "version": "1.2.4"})
    with pytest.raises(ValueError, match="machine-specific absolute paths"):
        replace(lock, command="/Users/example/bin/example-acp")
    with pytest.raises(ValueError, match="machine-specific absolute paths"):
        replace(lock, arguments=("--prefix=C:\\Users\\example",))
