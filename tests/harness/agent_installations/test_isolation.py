"""Platform network-deny launcher and production composition coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from gigaloom.harnesses.agent_profiles.installations import (
    AgentInstallError,
    create_agent_runtime_service,
)
from gigaloom.harnesses.agent_profiles.installations import composition
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAcpNetworkIsolation
from gigaloom.harnesses.agent_profiles.onboarding.isolation import (
    discover_managed_acp_network_isolation,
)


class HermeticIsolation:
    mechanism = "hermetic_test"

    def wrap(self, command, *, workspace, native_home):  # noqa: ANN001, ANN201
        del workspace, native_home
        return command


def _executable(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_macos_launcher_wraps_exact_command_with_network_deny(tmp_path):
    launcher = ManagedAcpNetworkIsolation(
        mechanism="macos_sandbox_exec",
        executable=str(_executable(tmp_path, "sandbox-exec")),
    )
    workspace = tmp_path / "workspace"
    native_home = tmp_path / "home"
    workspace.mkdir()
    native_home.mkdir()
    agent = _executable(tmp_path, "agent")

    command = launcher.wrap(
        (str(agent), "acp"),
        workspace=workspace,
        native_home=native_home,
    )

    assert command[:3] == (
        launcher.executable,
        "-p",
        "(version 1) (allow default) (deny network*)",
    )
    assert command[3:] == (str(agent), "acp")


def test_linux_launcher_uses_private_network_namespace_and_bounded_mounts(tmp_path):
    launcher = ManagedAcpNetworkIsolation(
        mechanism="linux_bwrap",
        executable=str(_executable(tmp_path, "bwrap")),
    )
    workspace = tmp_path / "workspace"
    native_home = tmp_path / "home"
    workspace.mkdir()
    native_home.mkdir()
    agent = _executable(tmp_path, "agent")

    command = launcher.wrap(
        (str(agent), "acp"),
        workspace=workspace,
        native_home=native_home,
    )

    assert command[:4] == (
        launcher.executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
    )
    assert command[-3:] == ("--", str(agent), "acp")
    assert command.count("--bind") == 2


def test_isolation_discovery_fails_closed_for_unsupported_platform():
    assert discover_managed_acp_network_isolation(platform_id="win32") is None


def test_runtime_composition_auto_admits_discovered_isolation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        composition,
        "discover_managed_acp_network_isolation",
        lambda **kwargs: HermeticIsolation(),
    )

    runtime = create_agent_runtime_service(
        tmp_path,
        platform_id="darwin",
        architecture="aarch64",
    )

    runtime.require_install_authority()


def test_runtime_composition_rejects_when_platform_has_no_isolation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        composition,
        "discover_managed_acp_network_isolation",
        lambda **kwargs: None,
    )
    runtime = create_agent_runtime_service(
        tmp_path,
        platform_id="win32",
        architecture="x86_64",
    )

    with pytest.raises(
        AgentInstallError,
        match="managed_agent_network_isolation_required",
    ):
        runtime.require_install_authority()
