"""Pure deterministic ACP Registry installation planner coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from gigaloom.contracts import ACPDistributionKind, AgentIntegrityPolicy
from gigaloom.harnesses.agent_profiles.installations import (
    AgentIdentityInventory,
    AgentInstallPlanner,
    AgentInstallPlannerPolicy,
    DistributionResolutionV1,
    InstallSelectionStatus,
)
from gigaloom.harnesses.agent_profiles.registry import decode_registry_document


NOW = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


def _catalog():
    payload = (
        Path(__file__).parents[2] / "fixtures/acp_registry/registry-v1-valid.json"
    ).read_bytes()
    return decode_registry_document(payload, fetched_at=NOW)


def _planner(
    tmp_path: Path, *, platform: str = "darwin", architecture: str = "aarch64"
):
    return AgentInstallPlanner(
        AgentInstallPlannerPolicy(
            platform=platform,
            architecture=architecture,
            data_root=str(tmp_path),
        )
    )


def _entry(registry_id: str):
    return next(item for item in _catalog().entries if item.registry_id == registry_id)


def _digest(character: str) -> str:
    return character * 64


def test_verified_current_binary_wins_and_every_alternative_is_explained(tmp_path):
    catalog = _catalog()
    entry = _entry("multi-distribution")
    binary = entry.distributions[0]
    npx = entry.distributions[1]
    result = _planner(tmp_path, platform="windows", architecture="x86_64").plan(
        entry,
        catalog.snapshot,
        resolutions=(
            DistributionResolutionV1(
                distribution_digest=npx.distribution_digest,
                artifact_digest=_digest("a"),
                package_integrity="sha512-YWJjZA==",
            ),
        ),
        now=NOW,
    )

    assert result.plan is not None
    assert result.plan.distribution_kind is ACPDistributionKind.BINARY
    assert result.plan.expected_integrity == binary.expected_integrity
    assert result.plan.managed_root.endswith(binary.expected_integrity)
    assert [item.status for item in result.decisions] == [
        InstallSelectionStatus.SELECTED,
        InstallSelectionStatus.REJECTED,
    ]
    assert result.decisions[1].reason_code == "lower_policy_preference"


def test_npx_and_uvx_require_exact_bound_resolution_evidence(tmp_path):
    catalog = _catalog()
    npx_entry = _entry("npx-exact")
    npx = npx_entry.distributions[0]
    missing = _planner(tmp_path).plan(npx_entry, catalog.snapshot, now=NOW)
    assert missing.plan is None
    assert missing.decisions[0].reason_code == "npm_integrity_resolution_required"

    resolved = _planner(tmp_path).plan(
        npx_entry,
        catalog.snapshot,
        resolutions=(
            DistributionResolutionV1(
                distribution_digest=npx.distribution_digest,
                artifact_digest=_digest("a"),
                package_integrity="sha512-YWJjZA==",
            ),
        ),
        now=NOW,
    )
    assert resolved.plan is not None
    assert resolved.plan.expected_integrity == "sha512-YWJjZA=="

    uvx_entry = _entry("uvx-exact")
    uvx = uvx_entry.distributions[0]
    uvx_result = _planner(tmp_path).plan(
        uvx_entry,
        catalog.snapshot,
        resolutions=(
            DistributionResolutionV1(
                distribution_digest=uvx.distribution_digest,
                artifact_digest=_digest("b"),
                lock_digest=_digest("c"),
                interpreter_fingerprint=_digest("d"),
            ),
        ),
        now=NOW,
    )
    assert uvx_result.plan is not None
    assert uvx_result.plan.distribution_kind is ACPDistributionKind.UVX
    assert uvx_result.plan.expected_integrity == _digest("b")


def test_unverified_binary_is_last_choice_with_elevated_confirmation(tmp_path):
    catalog = _catalog()
    entry = _entry("binary-unverified")
    result = _planner(tmp_path, platform="linux", architecture="x86_64").plan(
        entry,
        catalog.snapshot,
        now=NOW,
    )

    assert result.plan is not None
    assert (
        result.plan.integrity_policy is AgentIntegrityPolicy.ALLOW_EXPLICIT_UNVERIFIED
    )
    assert result.plan.confirmation_required is True
    assert "explicit_unverified_artifact_admission" in result.plan.side_effects


def test_platform_miss_is_fail_closed_and_repeatable(tmp_path):
    catalog = _catalog()
    entry = _entry("platform-miss")
    planner = _planner(tmp_path)
    first = planner.plan(entry, catalog.snapshot, now=NOW)
    second = planner.plan(entry, catalog.snapshot, now=NOW)

    assert first == second
    assert first.plan is None
    assert first.reason_code == "no_admissible_distribution"
    assert first.decisions[0].reason_code == "platform_or_architecture_mismatch"


def test_identity_collision_never_shadows_native_or_core_commands(tmp_path):
    catalog = _catalog()
    entry = _entry("binary-verified")
    inventory = AgentIdentityInventory(
        core_commands=("agent",),
        native_agent_ids=("binary-verified",),
        native_aliases=("gemini-cli",),
    )
    unresolved = _planner(tmp_path).plan(
        entry,
        catalog.snapshot,
        inventory=inventory,
        now=NOW,
    )

    assert unresolved.plan is None
    assert unresolved.proposed_local_agent_id == "binary-verified-acp"
    assert unresolved.collision_namespaces == ("native_agent",)
    assert all(
        item.reason_code == "identity_collision_requires_explicit_alias"
        for item in unresolved.decisions
    )

    resolved = _planner(tmp_path).plan(
        entry,
        catalog.snapshot,
        inventory=inventory,
        local_agent_id="binary-verified-acp",
        now=NOW,
    )
    assert resolved.plan is not None
    assert resolved.plan.local_agent_id == "binary-verified-acp"


def test_resolution_and_snapshot_evidence_cannot_cross_entry_boundaries(tmp_path):
    catalog = _catalog()
    entry = _entry("npx-exact")
    foreign = _entry("uvx-exact").distributions[0]
    try:
        _planner(tmp_path).plan(
            entry,
            catalog.snapshot,
            resolutions=(
                DistributionResolutionV1(
                    distribution_digest=foreign.distribution_digest,
                    artifact_digest=_digest("a"),
                ),
            ),
            now=NOW,
        )
    except ValueError as error:
        assert "not bound" in str(error)
    else:
        raise AssertionError("foreign resolution was accepted")
