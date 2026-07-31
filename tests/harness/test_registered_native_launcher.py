"""Generic registered-agent native launch contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from gigaloom.harnesses.agent_profiles import (
    AgentProfileRegistry,
    build_core_command_collision_contract,
    load_builtin_agent_profiles,
    load_local_agent_profile,
)
from gigaloom.native.launch import (
    NativeExecutableKind,
    NativeLaunchMode,
    PreparedRegisteredNativeLaunch,
    RegisteredNativeLaunchExecution,
    RegisteredNativeLaunchFailure,
    RegisteredNativeLaunchStatus,
    TerminalContext,
    execute_prepared_native_launch,
    launch_registered_native_agent,
    prepare_registered_native_launch,
)


FIXTURES = Path(__file__).parents[1] / "fixtures" / "agent_profiles"
PTY = TerminalContext(True, True, True, "xterm-256color", platform="darwin")
PIPE = TerminalContext(False, False, True, "xterm-256color", platform="darwin")


def _registry(tmp_path: Path) -> AgentProfileRegistry:
    manifest = tmp_path / "test-agent.toml"
    manifest.write_bytes((FIXTURES / "test-agent.toml").read_bytes())
    profiles = (*load_builtin_agent_profiles(), load_local_agent_profile(manifest))
    return AgentProfileRegistry.build(
        profiles,
        collision_contract=build_core_command_collision_contract(
            ("agent", "run", "ui")
        ),
    )


def _executable(path: Path, content: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_fake_fourth_agent_alias_uses_one_generic_launcher_and_exact_suffix(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = _executable(bin_dir / "test-agent-alt")
    calls: list[tuple[Path, tuple[str, ...], str, str]] = []

    def direct_runner(identity, suffix, environment, cwd):
        calls.append((identity.path, suffix, environment["MARKER"], cwd))
        return 37

    result = launch_registered_native_agent(
        ("ta", "--future", "Привет", "", "line one\nline two"),
        registry=registry,
        context=PTY,
        environment={"PATH": str(bin_dir), "MARKER": "kept"},
        cwd=tmp_path,
        direct_runner=direct_runner,
        managed_runner=lambda *_args: pytest.fail("unknown form must stay direct"),
    )

    assert result == RegisteredNativeLaunchExecution(
        agent_id="test-agent",
        mode=NativeLaunchMode.DIRECT_NATIVE,
        exit_code=37,
    )
    assert calls == [
        (
            executable.resolve(),
            ("--future", "Привет", "", "line one\nline two"),
            "kept",
            str(tmp_path),
        )
    ]


@pytest.mark.parametrize(
    ("suffix", "context", "expected_mode"),
    (
        (("--help",), PTY, NativeLaunchMode.DIRECT_NATIVE),
        (("exec", "--json"), PTY, NativeLaunchMode.DIRECT_NATIVE),
        ((), PIPE, NativeLaunchMode.DIRECT_NATIVE),
        ((), PTY, NativeLaunchMode.MANAGED_NATIVE),
    ),
)
def test_generic_launcher_preserves_profile_matcher_parity(
    tmp_path: Path,
    suffix: tuple[str, ...],
    context: TerminalContext,
    expected_mode: NativeLaunchMode,
) -> None:
    registry = _registry(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "test-agent")
    modes: list[NativeLaunchMode] = []

    def direct_runner(*_args):
        modes.append(NativeLaunchMode.DIRECT_NATIVE)
        return 0

    def managed_runner(*_args):
        modes.append(NativeLaunchMode.MANAGED_NATIVE)
        return 0

    result = launch_registered_native_agent(
        ("test-agent", *suffix),
        registry=registry,
        context=context,
        environment={"PATH": str(bin_dir)},
        direct_runner=direct_runner,
        managed_runner=managed_runner,
    )

    assert isinstance(result, RegisteredNativeLaunchExecution)
    assert result.mode is expected_mode
    assert modes == [expected_mode]


@pytest.mark.parametrize(
    ("target", "executable", "expected"),
    (
        ("missing", None, RegisteredNativeLaunchStatus.EXECUTABLE_MISSING),
        (
            "directory",
            "directory",
            RegisteredNativeLaunchStatus.EXECUTABLE_NON_EXECUTABLE,
        ),
        (
            "non-executable",
            "file",
            RegisteredNativeLaunchStatus.EXECUTABLE_NON_EXECUTABLE,
        ),
        ("recursive", "executable", RegisteredNativeLaunchStatus.EXECUTABLE_UNSAFE),
    ),
)
def test_prelaunch_failures_are_typed_and_never_run_provider(
    tmp_path: Path,
    target: str,
    executable: str | None,
    expected: RegisteredNativeLaunchStatus,
) -> None:
    registry = _registry(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    candidate = bin_dir / "test-agent"
    if executable == "directory":
        candidate.mkdir()
    elif executable == "file":
        candidate.write_text("not executable", encoding="utf-8")
    elif executable == "executable":
        _executable(candidate)
    facade = candidate if target == "recursive" else None

    result = launch_registered_native_agent(
        ("test-agent",),
        registry=registry,
        context=PTY,
        environment={"PATH": str(bin_dir)},
        facade_executable=facade,
        direct_runner=lambda *_args: pytest.fail("provider must not run"),
        managed_runner=lambda *_args: pytest.fail("provider must not run"),
    )

    assert result == RegisteredNativeLaunchFailure(
        status=expected,
        requested_token="test-agent",
        agent_id="test-agent",
    )


def test_core_command_and_unsupported_platform_do_not_become_native_launches(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)

    core = prepare_registered_native_launch(
        ("run",),
        registry=registry,
        context=PTY,
        environment={"PATH": ""},
    )
    unsupported = prepare_registered_native_launch(
        ("test-agent",),
        registry=registry,
        context=replace(PTY, platform="plan9"),
        environment={"PATH": ""},
    )

    assert core == RegisteredNativeLaunchFailure(
        RegisteredNativeLaunchStatus.NOT_AGENT,
        requested_token="run",
    )
    assert unsupported == RegisteredNativeLaunchFailure(
        RegisteredNativeLaunchStatus.UNSUPPORTED_PLATFORM,
        requested_token="test-agent",
        agent_id="test-agent",
    )


def test_executable_replacement_after_resolution_fails_closed(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = _executable(bin_dir / "real-agent")
    (bin_dir / "test-agent").symlink_to(target)
    prepared = prepare_registered_native_launch(
        ("test-agent",),
        registry=registry,
        context=PTY,
        environment={"PATH": str(bin_dir)},
    )
    assert isinstance(prepared, PreparedRegisteredNativeLaunch)
    target.unlink()
    _executable(target, "#!/bin/sh\nexit 9\n")

    result = execute_prepared_native_launch(
        prepared,
        direct_runner=lambda *_args: pytest.fail("drifted provider must not run"),
        managed_runner=lambda *_args: pytest.fail("drifted provider must not run"),
    )

    assert result == RegisteredNativeLaunchFailure(
        RegisteredNativeLaunchStatus.EXECUTABLE_DRIFTED,
        requested_token="test-agent",
        agent_id="test-agent",
    )


def test_windows_shim_is_planned_as_data_without_shell_execution(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = _executable(bin_dir / "test-agent.cmd")
    context = replace(PTY, platform="win32")
    prepared = prepare_registered_native_launch(
        ("test-agent", "--opaque", "a&b"),
        registry=registry,
        context=context,
        environment={"PATH": str(bin_dir), "PATHEXT": ".EXE;.CMD;.BAT"},
    )

    assert isinstance(prepared, PreparedRegisteredNativeLaunch)
    assert prepared.executable.path == shim.resolve()
    assert prepared.executable.kind is NativeExecutableKind.WINDOWS_SHIM
    assert prepared.provider_suffix == ("--opaque", "a&b")
    assert prepared.plan.mode is NativeLaunchMode.DIRECT_NATIVE
