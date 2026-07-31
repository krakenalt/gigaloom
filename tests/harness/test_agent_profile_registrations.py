"""Durable registration and probe-plan contracts for Agent Profiles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigaloom.completion import root_completion_candidates
from gigaloom.harnesses.agent_profiles import (
    AgentProbePlanStatus,
    AgentProfileRegistrationStore,
    build_core_command_collision_contract,
    load_agent_profile_registry,
    load_local_agent_profile,
    plan_agent_probe,
    register_local_agent_profile,
    remove_registered_agent_profile,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"
COLLISIONS = build_core_command_collision_contract(("agent", "run", "ui"))


def _manifest(tmp_path: Path, *, name: str = "test-agent.toml") -> Path:
    path = tmp_path / name
    path.write_bytes((FIXTURES / "test-agent.toml").read_bytes())
    return path


def test_registration_dry_run_apply_idempotency_and_remove_are_explicit(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "state"
    manifest = _manifest(tmp_path)
    executable = tmp_path / "test-agent"
    executable.write_text("provider-owned", encoding="utf-8")

    preview = register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
        dry_run=True,
    )
    assert preview.changed is True
    assert preview.next_revision == 1
    assert not AgentProfileRegistrationStore(data_dir).path.exists()

    applied = register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )
    repeated = register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )
    snapshot = load_agent_profile_registry(
        data_dir,
        collision_contract=COLLISIONS,
    )

    assert applied.changed is True and applied.next_revision == 1
    assert repeated.changed is False and repeated.next_revision == 1
    assert snapshot.registry.get("test-agent").aliases == ("ta",)
    assert snapshot.issues == ()
    assert (
        oct(AgentProfileRegistrationStore(data_dir).path.stat().st_mode & 0o777)
        == "0o600"
    )

    removed = remove_registered_agent_profile(data_dir, "test-agent")
    assert removed.next_revision == 2
    assert manifest.exists()
    assert executable.exists()
    assert [
        profile.agent_id
        for profile in load_agent_profile_registry(
            data_dir,
            collision_contract=COLLISIONS,
        ).registry.profiles
    ] == ["claude", "codex", "gemini"]


def test_registered_digest_drift_is_excluded_until_explicit_recovery(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "state"
    manifest = _manifest(tmp_path)
    register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'display_name = "Test Agent"',
            'display_name = "Changed Agent"',
        ),
        encoding="utf-8",
    )

    snapshot = load_agent_profile_registry(
        data_dir,
        collision_contract=COLLISIONS,
    )

    assert "test-agent" not in {
        profile.agent_id for profile in snapshot.registry.profiles
    }
    assert [(issue.agent_id, issue.code) for issue in snapshot.issues] == [
        ("test-agent", "profile_digest_drift")
    ]
    with pytest.raises(ValueError, match="remove it before replacement"):
        register_local_agent_profile(
            data_dir,
            manifest,
            collision_contract=COLLISIONS,
        )
    remove_registered_agent_profile(data_dir, "test-agent")
    register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )


def test_builtin_override_and_core_alias_collision_fail_closed(tmp_path: Path) -> None:
    payload = (FIXTURES / "test-agent.toml").read_text(encoding="utf-8")
    builtin = tmp_path / "builtin.toml"
    builtin.write_text(
        payload.replace('agent_id = "test-agent"', 'agent_id = "codex"'),
        encoding="utf-8",
    )
    collision = tmp_path / "collision.toml"
    collision.write_text(
        payload.replace('aliases = ["ta"]', 'aliases = ["run"]'),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cannot be overridden"):
        register_local_agent_profile(
            tmp_path / "state",
            builtin,
            collision_contract=COLLISIONS,
        )
    with pytest.raises(ValueError, match="collides with core commands: run"):
        register_local_agent_profile(
            tmp_path / "state",
            collision,
            collision_contract=COLLISIONS,
        )


def test_corrupt_registration_state_is_not_reset_or_silently_rewritten(
    tmp_path: Path,
) -> None:
    store = AgentProfileRegistrationStore(tmp_path / "state")
    store.directory.mkdir(parents=True)
    store.path.write_text(
        '{"schema_version": 1, "registrations": []}', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="keys are invalid"):
        store.read()
    assert json.loads(store.path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "registrations": [],
    }


def test_probe_planning_resolves_native_or_delegates_structured_without_execution(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    profile = load_local_agent_profile(manifest)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "test-agent"
    executable.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    executable.chmod(0o755)
    marker = tmp_path / "must-not-exist"

    native = plan_agent_probe(
        profile,
        environment={"PATH": str(bin_dir), "MARKER": str(marker)},
        platform="darwin",
    )
    missing_route = plan_agent_probe(
        profile,
        route_id="missing.route",
        environment={"PATH": str(bin_dir)},
        platform="darwin",
    )

    assert native.status is AgentProbePlanStatus.PLANNED
    assert native.executable_path == str(executable.resolve())
    assert native.arguments == ("--version",)
    assert native.execution_performed is False
    assert missing_route.status is AgentProbePlanStatus.ROUTE_NOT_FOUND
    assert missing_route.owner == "structured_transport"
    assert not marker.exists()


def test_one_thousand_profiles_are_bounded_for_completion() -> None:
    agent_ids = tuple(f"agent-{index:04d}" for index in range(1_000))

    candidates = root_completion_candidates(agent_ids=agent_ids)

    assert candidates[-1] == "agent-0999"
    assert len(candidates) == len(set(candidates))
    with pytest.raises(ValueError, match="too large"):
        root_completion_candidates(agent_ids=(*agent_ids, "agent-overflow"))
