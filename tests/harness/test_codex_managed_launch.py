"""Managed app-server and real Codex TUI launch contracts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gigaloom.native.codex_operator import (
    CODEX_MAXIMUM_VERSION_EXCLUSIVE,
    CODEX_MINIMUM_VERSION,
    CODEX_SCHEMA_BUNDLE_SHA256,
    CodexCapabilityState,
    CodexCompatibilitySnapshot,
    CodexManagedLaunchError,
    CodexManagedLaunchRequest,
    CodexManagedTerminalLauncher,
)
from gigaloom.native.codex_operator.app_server import (
    CodexAppServerLaunchSpec,
    CodexAppServerProcess,
    CodexAppServerProcessError,
)
from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalAccess,
    TerminalState,
    terminal_record_to_dict,
)


class _FakeAppServer:
    def __init__(self, spec: CodexAppServerLaunchSpec) -> None:
        self.spec = spec
        self.alive = False
        self.closed = False

    def start(self) -> None:
        self.alive = True

    def close(self) -> None:
        self.alive = False
        self.closed = True


class _FakeTerminalKernel:
    def __init__(self) -> None:
        self.launches = []
        self.closed = []

    def launch(self, record, spec):
        self.launches.append((record, spec))
        return TerminalState.RUNNING

    def close(self, record):
        self.closed.append(record.id)


class _FakeProcess:
    def __init__(self, *, returncode=None) -> None:
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        del timeout
        return self.returncode or 0


def _compatibility(
    state: CodexCapabilityState = CodexCapabilityState.SUPPORTED,
) -> CodexCompatibilitySnapshot:
    return CodexCompatibilitySnapshot(
        status=state,
        executable_version="codex-cli 0.144.5",
        parsed_version="0.144.5",
        minimum_version=CODEX_MINIMUM_VERSION,
        maximum_version_exclusive=CODEX_MAXIMUM_VERSION_EXCLUSIVE,
        schema_bundle_sha256=CODEX_SCHEMA_BUNDLE_SHA256,
        capabilities={"native_tui": state},
        transport="unix" if state is CodexCapabilityState.SUPPORTED else None,
        reason_code="fixture",
    )


def _request(tmp_path: Path) -> CodexManagedLaunchRequest:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return CodexManagedLaunchRequest(
        access=TerminalAccess(
            owner_id="owner-1",
            workspace_id="workspace-1",
            session_id="session-1",
        ),
        terminal_name="codex",
        session_key="primary",
        command=("/opt/fixture/codex",),
        executable_version="0.144.5",
        cwd=workspace,
        env={"PROVIDER_TOKEN": "fixture-secret"},
        config_toml='model = "fixture"\n',
    )


def _socket_root(tmp_path: Path) -> Path:
    return Path("/tmp") / f"gigaloom-codex-{tmp_path.name}"


def test_launch_binds_private_app_server_to_real_remote_tui(tmp_path):
    registry = ManagedTerminalRegistry(id_factory=lambda: "term_fixture")
    kernel = _FakeTerminalKernel()
    servers = []

    def factory(spec):
        server = _FakeAppServer(spec)
        servers.append(server)
        return server

    launcher = CodexManagedTerminalLauncher(
        tmp_path / "state",
        registry,
        kernel,
        _compatibility(),
        socket_root=_socket_root(tmp_path),
        app_server_factory=factory,
    )
    request = _request(tmp_path)

    launched = launcher.launch(request)

    assert launched.terminal.state is TerminalState.RUNNING
    assert launched.compatibility_status is CodexCapabilityState.SUPPORTED
    assert len(servers) == 1
    server = servers[0]
    assert server.alive is True
    assert server.spec.command[:2] == ("/opt/fixture/codex", "app-server")
    assert server.spec.command[-1] == "--strict-config"
    assert server.spec.command[3].startswith("unix://")
    assert server.spec.env["CODEX_HOME"].endswith("/home")
    assert server.spec.env["PROVIDER_TOKEN"] == "fixture-secret"
    record, terminal_spec = kernel.launches[0]
    assert record.id == "term_fixture"
    assert terminal_spec.command[:3] == (
        "/opt/fixture/codex",
        "--remote",
        server.spec.command[3],
    )
    assert terminal_spec.command[-2:] == ("--cd", str(request.cwd))
    assert terminal_spec.env["CODEX_HOME"] == server.spec.env["CODEX_HOME"]
    home = Path(server.spec.env["CODEX_HOME"])
    assert home.stat().st_mode & 0o777 == 0o700
    assert (home / "config.toml").read_text() == 'model = "fixture"\n'
    assert (home / "config.toml").stat().st_mode & 0o777 == 0o600
    public = terminal_record_to_dict(launched.terminal)
    assert "socket" not in repr(public).lower()
    assert str(home) not in repr(public)
    assert "fixture-secret" not in repr(public)


def test_duplicate_launch_reuses_one_terminal_and_app_server(tmp_path):
    registry = ManagedTerminalRegistry(id_factory=lambda: "term_fixture")
    kernel = _FakeTerminalKernel()
    servers = []

    def factory(spec):
        server = _FakeAppServer(spec)
        servers.append(server)
        return server

    launcher = CodexManagedTerminalLauncher(
        tmp_path / "state",
        registry,
        kernel,
        _compatibility(),
        socket_root=_socket_root(tmp_path),
        app_server_factory=factory,
    )
    request = _request(tmp_path)

    first = launcher.launch(request)
    second = launcher.launch(request)

    assert second.terminal == first.terminal
    assert len(servers) == 1
    assert len(kernel.launches) == 1


def test_close_stops_only_paired_terminal_and_app_server(tmp_path):
    registry = ManagedTerminalRegistry(id_factory=lambda: "term_fixture")
    kernel = _FakeTerminalKernel()
    servers = []

    def factory(spec):
        server = _FakeAppServer(spec)
        servers.append(server)
        return server

    launcher = CodexManagedTerminalLauncher(
        tmp_path / "state",
        registry,
        kernel,
        _compatibility(),
        socket_root=_socket_root(tmp_path),
        app_server_factory=factory,
    )
    request = _request(tmp_path)
    launched = launcher.launch(request)

    closed = launcher.close(
        launched.terminal.id,
        request.access,
        expected_revision=launched.terminal.revision,
    )

    assert closed.state is TerminalState.CLOSED
    assert kernel.closed == ["term_fixture"]
    assert servers[0].closed is True


def test_native_only_compatibility_refuses_synthetic_remote_launch(tmp_path):
    launcher = CodexManagedTerminalLauncher(
        tmp_path / "state",
        ManagedTerminalRegistry(),
        _FakeTerminalKernel(),
        _compatibility(CodexCapabilityState.NATIVE_ONLY),
        socket_root=_socket_root(tmp_path),
    )

    with pytest.raises(
        CodexManagedLaunchError,
        match="structured native Codex launch is not admitted",
    ):
        launcher.launch(_request(tmp_path))


def test_launch_request_rejects_invalid_arguments_and_config(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    access = TerminalAccess("owner", "workspace", "session")

    with pytest.raises(ValueError, match="arguments"):
        CodexManagedLaunchRequest(
            access=access,
            terminal_name="codex",
            session_key="primary",
            command=("/fixture/codex", "bad\0arg"),
            executable_version="0.144.5",
            cwd=workspace,
        )
    with pytest.raises(ValueError, match="config"):
        CodexManagedLaunchRequest(
            access=access,
            terminal_name="codex",
            session_key="primary",
            command=("/fixture/codex",),
            executable_version="0.144.5",
            cwd=workspace,
            config_toml="\0",
        )
    assert "CODEX_HOME" not in os.environ


def test_private_app_server_waits_for_socket_and_closes_owned_process(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_directory = _socket_root(tmp_path) / "server"
    spec = CodexAppServerLaunchSpec(
        command=(
            "/fixture/codex",
            "app-server",
            "--listen",
            f"unix://{socket_directory / 's'}",
        ),
        env={"CODEX_HOME": str(tmp_path / "home")},
        cwd=workspace,
        socket_directory=socket_directory,
        socket_path=socket_directory / "s",
    )
    process = _FakeProcess()
    popen_calls = []
    ready_calls = 0

    def popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        return process

    def socket_ready(path):
        nonlocal ready_calls
        assert path == spec.socket_path
        ready_calls += 1
        return ready_calls == 2

    server = CodexAppServerProcess(
        spec,
        popen=popen,
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        socket_ready=socket_ready,
    )

    server.start()
    assert server.alive is True
    assert len(popen_calls) == 1
    assert popen_calls[0][0][0] == spec.command
    assert popen_calls[0][1]["env"]["CODEX_HOME"] == str(tmp_path / "home")
    assert popen_calls[0][1]["start_new_session"] is True

    server.close()
    assert server.alive is False
    assert process.terminated is True
    assert socket_directory.exists() is False


def test_private_app_server_rejects_exit_before_socket_readiness(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    socket_directory = _socket_root(tmp_path) / "server"
    spec = CodexAppServerLaunchSpec(
        command=("/fixture/codex", "app-server"),
        env={},
        cwd=workspace,
        socket_directory=socket_directory,
        socket_path=socket_directory / "s",
    )
    server = CodexAppServerProcess(
        spec,
        popen=lambda *_, **__: _FakeProcess(returncode=2),
        monotonic=lambda: 0.0,
        sleep=lambda _: None,
        socket_ready=lambda _: False,
    )

    with pytest.raises(
        CodexAppServerProcessError,
        match="exited before socket readiness",
    ):
        server.start()
