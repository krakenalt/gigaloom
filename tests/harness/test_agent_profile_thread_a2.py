"""A2 closure: dynamic namespace parity, collisions, bounds, and recovery."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

import pytest

from gigaloom.completion import root_completion_candidates
from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistrationStore,
    AgentProfileRegistry,
    build_core_command_collision_contract,
    load_agent_profile_registry,
    load_builtin_agent_profiles,
    load_local_agent_profile,
    register_local_agent_profile,
    remove_registered_agent_profile,
)
from gigaloom.native.launch import (
    NativeLaunchMode,
    RegisteredNativeLaunchExecution,
    TerminalContext,
    launch_registered_native_agent,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"
COLLISIONS = build_core_command_collision_contract(("agent", "run", "ui"))
PIPE = TerminalContext(False, False, True, "xterm-256color", platform="darwin")


def _manifest(tmp_path: Path) -> Path:
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes((FIXTURES / "test-agent.toml").read_bytes())
    return manifest


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_registered_fourth_agent_launches_without_root_router_edit_or_persistence(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "state"
    manifest = _manifest(tmp_path)
    register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )
    snapshot = load_agent_profile_registry(
        data_dir,
        collision_contract=COLLISIONS,
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = _executable(bin_dir / "test-agent")
    calls = []

    result = launch_registered_native_agent(
        ("ta", "--provider-owned", "must-never-persist"),
        registry=snapshot.registry,
        context=PIPE,
        environment={"PATH": str(bin_dir)},
        cwd=tmp_path,
        direct_runner=lambda identity, suffix, _environment, cwd: (
            calls.append((identity.path, suffix, cwd)) or 23
        ),
        managed_runner=lambda *_args: pytest.fail("pipe invocation must stay direct"),
    )

    assert result == RegisteredNativeLaunchExecution(
        agent_id="test-agent",
        mode=NativeLaunchMode.DIRECT_NATIVE,
        exit_code=23,
    )
    assert calls == [
        (
            executable.resolve(),
            ("--provider-owned", "must-never-persist"),
            str(tmp_path),
        )
    ]
    registration_text = AgentProfileRegistrationStore(data_dir).path.read_text(
        encoding="utf-8"
    )
    assert "must-never-persist" not in registration_text
    assert "test-agent" not in (
        Path(__file__).parents[2] / "src" / "gigaloom" / "entrypoint.py"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("agent_id", ("codex", "claude", "gemini", "pi"))
def test_builtin_profiles_keep_direct_native_parity_through_generic_launcher(
    tmp_path: Path,
    agent_id: str,
) -> None:
    registry = AgentProfileRegistry.build(
        load_builtin_agent_profiles(),
        collision_contract=COLLISIONS,
    )
    bin_dir = tmp_path / agent_id
    bin_dir.mkdir()
    executable = _executable(bin_dir / agent_id)
    calls = []

    result = launch_registered_native_agent(
        (agent_id, "--help"),
        registry=registry,
        context=PIPE,
        environment={"PATH": str(bin_dir)},
        direct_runner=lambda identity, suffix, _environment, _cwd: (
            calls.append((identity.path, suffix)) or 0
        ),
        managed_runner=lambda *_args: pytest.fail("metadata must stay direct"),
    )

    assert result == RegisteredNativeLaunchExecution(
        agent_id=agent_id,
        mode=NativeLaunchMode.DIRECT_NATIVE,
        exit_code=0,
    )
    assert calls == [(executable.resolve(), ("--help",))]


def test_duplicate_ids_aliases_and_builtin_override_attempts_fail_closed(
    tmp_path: Path,
) -> None:
    profile = load_local_agent_profile(_manifest(tmp_path))
    duplicate_alias = replace(
        profile,
        agent_id="other-agent",
        aliases=("ta",),
    )
    id_over_alias = replace(
        profile,
        agent_id="ta",
        aliases=(),
    )

    with pytest.raises(ValueError, match="duplicate agent profile id"):
        AgentProfileRegistry.build(
            (profile, profile),
            collision_contract=COLLISIONS,
        )
    with pytest.raises(ValueError, match="duplicate agent profile alias"):
        AgentProfileRegistry.build(
            (profile, duplicate_alias),
            collision_contract=COLLISIONS,
        )
    with pytest.raises(ValueError, match="collides with an alias"):
        AgentProfileRegistry.build(
            (profile, id_over_alias),
            collision_contract=COLLISIONS,
        )


def test_one_thousand_profile_index_and_completion_stay_bounded(tmp_path: Path) -> None:
    profile = load_local_agent_profile(_manifest(tmp_path))
    profiles = tuple(
        replace(profile, agent_id=f"agent-{index:04d}", aliases=())
        for index in range(1_000)
    )
    registry = AgentProfileRegistry.build(
        profiles,
        collision_contract=COLLISIONS,
    )

    started = time.perf_counter()
    resolution = registry.resolve("agent-0999")
    elapsed = time.perf_counter() - started
    completions = root_completion_candidates(
        agent_ids=(item.agent_id for item in registry.profiles)
    )

    assert elapsed < 0.05
    assert resolution.agent_id == "agent-0999"
    assert completions[-1] == "agent-0999"
    assert len(registry.profiles) == 1_000
    with pytest.raises(ValueError, match="registry is too large"):
        AgentProfileRegistry.build(
            (*profiles, replace(profile, agent_id="agent-overflow", aliases=())),
            collision_contract=COLLISIONS,
        )


def test_stale_or_interrupted_registration_can_be_removed_without_provider_mutation(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "state"
    manifest = _manifest(tmp_path)
    provider_artifact = _executable(tmp_path / "test-agent-provider")
    register_local_agent_profile(
        data_dir,
        manifest,
        collision_contract=COLLISIONS,
    )
    store = AgentProfileRegistrationStore(data_dir)
    interrupted = store.directory / ".registrations-interrupted.tmp"
    interrupted.write_text("partial", encoding="utf-8")
    manifest.unlink()

    stale = load_agent_profile_registry(
        data_dir,
        collision_contract=COLLISIONS,
    )
    assert [(issue.agent_id, issue.code) for issue in stale.issues] == [
        ("test-agent", "manifest_unavailable")
    ]
    removed = remove_registered_agent_profile(data_dir, "test-agent")

    assert removed.changed is True
    assert provider_artifact.exists()
    assert interrupted.read_text(encoding="utf-8") == "partial"
    assert (
        load_agent_profile_registry(
            data_dir,
            collision_contract=COLLISIONS,
        ).issues
        == ()
    )


def test_registration_store_rejects_dangling_symlink_instead_of_resetting(
    tmp_path: Path,
) -> None:
    store = AgentProfileRegistrationStore(tmp_path / "state")
    store.directory.mkdir(parents=True)
    store.path.symlink_to(tmp_path / "missing-state.json")

    with pytest.raises(ValueError, match="regular file"):
        store.read()
    assert store.path.is_symlink()
