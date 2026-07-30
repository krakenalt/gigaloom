from datetime import datetime, timezone
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time

import pytest

from gigaloom.native.terminal import (
    TerminalIdentity,
    TerminalLivenessKind,
    TerminalRecord,
    TerminalState,
    TmuxCapability,
    TmuxCapabilityStatus,
    TmuxInstanceError,
    TmuxLaunchSpec,
    TmuxTerminalKernel,
    digest_terminal_command,
    digest_terminal_path,
    probe_tmux,
)
from gigaloom.native.terminal.tmux import (
    TmuxCommandResult,
    TmuxCommandTimeoutError,
)


def test_tmux_probe_reports_platform_missing_invalid_and_available():
    runner = FakeTmuxRunner(responses=(TmuxCommandResult(0, stdout=b"tmux 3.7b\n"),))

    unsupported = probe_tmux(platform="win32")
    missing = probe_tmux(resolve_executable=lambda _: None, platform="linux")
    invalid = probe_tmux(
        runner=FakeTmuxRunner(
            responses=(TmuxCommandResult(0, stdout=b"unexpected\n"),)
        ),
        resolve_executable=lambda _: "/usr/bin/tmux",
        platform="linux",
    )
    available = probe_tmux(
        runner=runner,
        resolve_executable=lambda _: "/usr/bin/tmux",
        platform="linux",
    )

    assert unsupported.status is TmuxCapabilityStatus.UNSUPPORTED
    assert missing.status is TmuxCapabilityStatus.MISSING
    assert invalid.status is TmuxCapabilityStatus.INVALID
    assert available == TmuxCapability(
        status=TmuxCapabilityStatus.AVAILABLE,
        executable_path="/usr/bin/tmux",
        version="3.7b",
        reason="tmux_is_available",
    )
    assert runner.calls[0].argv == ("/usr/bin/tmux", "-V")


def test_tmux_probe_bounds_timeout_as_invalid():
    capability = probe_tmux(
        runner=TimeoutRunner(),
        resolve_executable=lambda _: "/usr/bin/tmux",
        platform="linux",
    )

    assert capability.status is TmuxCapabilityStatus.INVALID
    assert capability.reason == "tmux_version_probe_failed"


@pytest.mark.parametrize("scrollback", (99, 100_001, True))
def test_tmux_kernel_rejects_unbounded_scrollback(tmp_path, scrollback):
    with pytest.raises(ValueError, match="scrollback"):
        TmuxTerminalKernel(
            tmp_path,
            _capability(),
            runner=FakeTmuxRunner(),
            scrollback_lines=scrollback,
        )


def test_private_tmux_launch_uses_digest_targets_and_bounded_config(
    tmp_path,
    short_socket_root,
):
    spec = _spec(tmp_path)
    record = _record(spec)
    runner = FakeTmuxRunner(liveness=b"0\t\t4242\n", create_socket=True)
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=runner,
        socket_root=short_socket_root,
        scrollback_lines=321,
    )

    state = kernel.launch(record, spec)

    assert state is TerminalState.RUNNING
    launch = runner.calls[0]
    socket_path = Path(launch.argv[2])
    assert launch.argv[:6] == (
        "/usr/bin/tmux",
        "-S",
        str(socket_path),
        "-f",
        "/dev/null",
        "start-server",
    )
    assert ("set-option", "-gw", "remain-on-exit", "on") == _command_slice(
        launch.argv,
        "remain-on-exit",
    )
    assert ("set-option", "-gw", "history-limit", "321") == _command_slice(
        launch.argv,
        "history-limit",
    )
    assert "owner-1" not in " ".join(launch.argv)
    assert "workspace-1" not in " ".join(launch.argv)
    assert "session-key-1" not in " ".join(launch.argv)
    assert stat.S_IMODE(socket_path.parent.stat().st_mode) == 0o700
    assert launch.env is not None
    assert "TMUX" not in launch.env
    assert "TMUX_PANE" not in launch.env
    assert launch.env["SAFE"] == "value"


def test_liveness_distinguishes_dead_pane_from_missing_server(
    tmp_path,
    short_socket_root,
):
    spec = _spec(tmp_path)
    record = _record(spec)
    runner = FakeTmuxRunner(liveness=b"1\t17\t4242\n", create_socket=True)
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=runner,
        socket_root=short_socket_root,
    )

    state = kernel.launch(record, spec)
    observed = kernel.liveness(record.id)
    socket_path = Path(runner.calls[0].argv[2])
    socket_path.unlink()
    missing = kernel.liveness(record.id)

    assert state is TerminalState.EXITED
    assert observed.kind is TerminalLivenessKind.EXITED
    assert observed.exit_status == 17
    assert observed.pane_pid == 4242
    assert missing.kind is TerminalLivenessKind.MISSING
    assert missing.terminal_state is TerminalState.ORPHANED


def test_close_targets_only_private_server_and_removes_known_directory(
    tmp_path,
    short_socket_root,
):
    spec = _spec(tmp_path)
    record = _record(spec)
    runner = FakeTmuxRunner(liveness=b"0\t\t4242\n", create_socket=True)
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=runner,
        socket_root=short_socket_root,
    )
    kernel.launch(record, spec)
    socket_path = Path(runner.calls[0].argv[2])

    kernel.close(record)

    assert runner.calls[-1].argv[-2:] == ("-N", "kill-server")
    assert not socket_path.exists()
    assert not socket_path.parent.exists()


def test_capture_seed_restores_alternate_screen_cursor_and_bytes(
    tmp_path,
    short_socket_root,
):
    spec = _spec(tmp_path)
    record = _record(spec)
    runner = FakeTmuxRunner(liveness=b"0\t\t4242\n", create_socket=True)
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=runner,
        socket_root=short_socket_root,
    )
    kernel.launch(record, spec)
    runner.responses.extend(
        (
            TmuxCommandResult(0, stdout=b"1\t4\t2\t80\t24\n"),
            TmuxCommandResult(0, stdout=b"hello\\012\\377"),
        )
    )

    seed = kernel.capture_seed(record.id)
    kernel.close(record)

    assert seed == b"\x1b[?1049h\x1b[2J\x1b[Hhello\n\xff\x1b[3;5H"


def test_launch_binding_fails_before_tmux_on_changed_command(tmp_path):
    spec = _spec(tmp_path)
    record = _record(spec)
    changed = TmuxLaunchSpec(
        command=(*spec.command, "--changed"),
        cwd=spec.cwd,
    )
    runner = FakeTmuxRunner()
    kernel = TmuxTerminalKernel(tmp_path, _capability(), runner=runner)

    with pytest.raises(TmuxInstanceError, match="command digest changed"):
        kernel.launch(record, changed)

    assert runner.calls == []


def test_launch_failure_is_content_free_and_scoped(tmp_path, short_socket_root):
    spec = _spec(tmp_path)
    record = _record(spec)
    runner = FakeTmuxRunner(
        responses=(TmuxCommandResult(1, stderr=b"secret command detail"),)
    )
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=runner,
        socket_root=short_socket_root,
    )

    with pytest.raises(TmuxInstanceError) as raised:
        kernel.launch(record, spec)

    assert str(raised.value) == "private tmux launch failed"
    assert "secret" not in str(raised.value)


def test_launch_timeout_is_content_free_and_cleans_private_directories(
    tmp_path,
    short_socket_root,
):
    spec = _spec(tmp_path)
    record = _record(spec)
    kernel = TmuxTerminalKernel(
        tmp_path,
        _capability(),
        runner=TimeoutRunner(),
        socket_root=short_socket_root,
    )

    with pytest.raises(TmuxInstanceError) as raised:
        kernel.launch(record, spec)

    assert str(raised.value) == "private tmux launch failed"
    assert not any(kernel.state_root.iterdir())
    assert not any(short_socket_root.iterdir())


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_private_tmux_retains_immediate_exit_and_closes(
    tmp_path,
    short_socket_root,
):
    capability = probe_tmux()
    spec = TmuxLaunchSpec(
        command=(sys.executable, "-c", "raise SystemExit(7)"),
        cwd=tmp_path,
    )
    record = _record(spec, terminal_id="term_real")
    kernel = TmuxTerminalKernel(
        tmp_path,
        capability,
        socket_root=short_socket_root,
    )

    state = kernel.launch(record, spec)
    observed = kernel.liveness(record.id)
    for _ in range(100):
        if observed.kind is TerminalLivenessKind.EXITED:
            break
        time.sleep(0.01)
        observed = kernel.liveness(record.id)
    kernel.close(record)

    assert state in {TerminalState.RUNNING, TerminalState.EXITED}
    assert observed.kind is TerminalLivenessKind.EXITED
    assert observed.exit_status == 7


class Call:
    def __init__(self, argv, env):
        self.argv = tuple(argv)
        self.env = None if env is None else dict(env)


class FakeTmuxRunner:
    def __init__(
        self,
        *,
        responses=(),
        liveness=b"0\t\t4242\n",
        create_socket=False,
    ):
        self.responses = list(responses)
        self.liveness = liveness
        self.create_socket = create_socket
        self.calls = []

    def run(
        self,
        argv,
        *,
        env=None,
        timeout_seconds=5.0,
        max_output_bytes=4096,
    ):
        del timeout_seconds, max_output_bytes
        call = Call(argv, env)
        self.calls.append(call)
        if self.responses:
            return self.responses.pop(0)
        if "new-session" in argv and self.create_socket:
            socket_path = Path(argv[argv.index("-S") + 1])
            socket_path.touch()
            return TmuxCommandResult(0)
        if "list-panes" in argv:
            return TmuxCommandResult(0, stdout=self.liveness)
        if "kill-server" in argv:
            socket_path = Path(argv[argv.index("-S") + 1])
            socket_path.unlink(missing_ok=True)
            return TmuxCommandResult(0)
        return TmuxCommandResult(0)


class TimeoutRunner:
    def run(
        self,
        argv,
        *,
        env=None,
        timeout_seconds=5.0,
        max_output_bytes=4096,
    ):
        del argv, env, timeout_seconds, max_output_bytes
        raise TmuxCommandTimeoutError("timeout")


@pytest.fixture
def short_socket_root():
    root = Path(tempfile.mkdtemp(prefix="gl-tmux-test-", dir="/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _capability():
    return TmuxCapability(
        status=TmuxCapabilityStatus.AVAILABLE,
        executable_path="/usr/bin/tmux",
        version="3.7b",
        reason="tmux_is_available",
    )


def _spec(tmp_path):
    executable = Path(sys.executable).resolve()
    return TmuxLaunchSpec(
        command=(str(executable), "-c", "print('ok')"),
        cwd=tmp_path,
        env={"SAFE": "value"},
    )


def _record(spec, *, terminal_id="term_test"):
    identity = TerminalIdentity(
        owner_id="owner-1",
        workspace_id="workspace-1",
        session_id="session-1",
        terminal_name="codex",
        session_key="session-key-1",
        native_harness_id="codex-cli",
        command_digest=digest_terminal_command(spec.command),
        cwd_digest=digest_terminal_path(spec.cwd),
        executable_path_digest=digest_terminal_path(spec.command[0]),
        executable_version="test",
    )
    timestamp = datetime(2026, 7, 30, 9, tzinfo=timezone.utc).isoformat()
    return TerminalRecord(
        id=terminal_id,
        identity=identity,
        state=TerminalState.STARTING,
        revision=1,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _command_slice(argv, option):
    index = argv.index(option) - 2
    return argv[index : index + 4]
