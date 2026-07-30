from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import shutil
import tempfile

import pytest

from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalIdentity,
    TerminalLiveness,
    TerminalLivenessKind,
    TerminalState,
    TmuxLaunchSpec,
    TmuxTerminalKernel,
    digest_terminal_command,
    digest_terminal_path,
    probe_tmux,
)
from gigaloom.native.terminal.control_bridge import (
    BoundedTerminalOutputQueue,
    SubprocessTmuxControlClient,
    TerminalBackpressureError,
    TerminalControlBridge,
    TerminalControlSession,
)
from gigaloom.native.terminal.security import (
    TerminalAttachRejectedError,
    TerminalAttachRequest,
    TerminalOriginPolicy,
)
from gigaloom.native.terminal.tmux import decode_tmux_escaped_bytes
from gigaloom.native.terminal.websocket_protocol import (
    MAX_TERMINAL_INPUT_CHUNK_BYTES,
    TerminalInputFrame,
    TerminalResizeFrame,
    TerminalWebSocketCloseCode,
    parse_terminal_client_frame,
    terminal_resize_frame_json,
)


_DIGEST = "a" * 64
_ORIGIN = "http://127.0.0.1:8090"


def test_binary_input_and_resize_are_strict_separate_frame_types():
    payload = b"\x00\xffhello"
    resize_json = terminal_resize_frame_json(rows=40, columns=120, revision=7)

    assert parse_terminal_client_frame(payload) == TerminalInputFrame(payload)
    assert parse_terminal_client_frame(resize_json) == TerminalResizeFrame(
        rows=40,
        columns=120,
        revision=7,
    )
    with pytest.raises(ValueError, match="fields"):
        parse_terminal_client_frame(
            json.dumps(
                {
                    "type": "resize",
                    "rows": 40,
                    "columns": 120,
                    "revision": 7,
                    "extra": True,
                }
            )
        )
    with pytest.raises(ValueError, match="rows"):
        parse_terminal_client_frame(
            '{"type":"resize","rows":true,"columns":80,"revision":7}'
        )


def test_tmux_octal_decoder_preserves_arbitrary_bytes():
    assert decode_tmux_escaped_bytes(b"hello\\012\\134\\377") == b"hello\n\\\xff"
    with pytest.raises(ValueError, match="truncated"):
        decode_tmux_escaped_bytes(b"bad\\12")
    with pytest.raises(ValueError, match="invalid"):
        decode_tmux_escaped_bytes(b"bad\\xyz")


def test_output_queue_coalesces_splits_and_fails_closed_on_backpressure():
    queue = BoundedTerminalOutputQueue(max_frame_bytes=4, max_queue_bytes=8)

    queue.push(b"ab")
    queue.push(b"cdef")

    assert queue.queued_bytes == 6
    assert queue.pop() == b"abcd"
    assert queue.pop() == b"ef"
    queue.push(b"12345678")
    with pytest.raises(TerminalBackpressureError):
        queue.push(b"9")


def test_bridge_authorizes_origin_binding_and_revision_before_backend_resolution():
    registry, record = _registry()
    backend = FakeBackend()
    bridge = TerminalControlBridge(
        registry,
        backend,
        TerminalOriginPolicy((_ORIGIN,)),
    )

    session = bridge.open(_request(record))

    assert backend.calls == [("capture", record.id), ("open", record.id)]
    assert session.next_frame() == b"seed"

    backend.calls.clear()
    with pytest.raises(TerminalAttachRejectedError) as wrong_origin:
        bridge.open(_request(record, origin="https://evil.example"))
    assert wrong_origin.value.code is TerminalWebSocketCloseCode.FORBIDDEN
    assert backend.calls == []

    with pytest.raises(TerminalAttachRejectedError) as wrong_owner:
        bridge.open(_request(record, owner_id="owner-2"))
    assert wrong_owner.value.code is TerminalWebSocketCloseCode.FORBIDDEN
    assert backend.calls == []

    with pytest.raises(TerminalAttachRejectedError) as stale:
        bridge.open(_request(record, revision=record.revision + 1))
    assert stale.value.code is TerminalWebSocketCloseCode.FORBIDDEN
    assert backend.calls == []


def test_browser_input_is_byte_exact_chunked_and_resize_is_revision_bound():
    registry, record = _registry()
    backend = FakeBackend()
    session = TerminalControlBridge(
        registry,
        backend,
        TerminalOriginPolicy((_ORIGIN,)),
    ).open(_request(record))
    payload = bytes(range(256)) * 40

    session.receive(payload)
    session.receive(
        terminal_resize_frame_json(
            rows=50,
            columns=140,
            revision=record.revision,
        )
    )
    session.flush_resize()

    assert b"".join(backend.client.inputs) == payload
    assert max(map(len, backend.client.inputs)) == MAX_TERMINAL_INPUT_CHUNK_BYTES
    assert backend.client.resizes == [(50, 140)]
    with pytest.raises(TerminalAttachRejectedError) as stale:
        session.receive(
            terminal_resize_frame_json(
                rows=50,
                columns=140,
                revision=record.revision + 1,
            )
        )
    assert stale.value.code is TerminalWebSocketCloseCode.FORBIDDEN


def test_resize_storm_coalesces_to_latest_bounded_frame():
    client = FakeClient()
    record = _record()
    session = TerminalControlSession(
        record,
        client,
        liveness=lambda _: TerminalLiveness(TerminalLivenessKind.LIVE),
        seed=b"",
    )
    for rows, columns in ((30, 90), (40, 100), (50, 120)):
        session.receive(
            terminal_resize_frame_json(
                rows=rows,
                columns=columns,
                revision=record.revision,
            )
        )

    session.flush_resize()

    assert client.resizes == [(50, 120)]


def test_control_output_is_binary_and_exit_reasons_are_explicit():
    client = FakeClient(
        lines=[
            b"%output %1 hello\\012\\377\n",
            b"%pane-died %1\n",
        ]
    )
    record = _record()
    session = TerminalControlSession(
        record,
        client,
        liveness=lambda _: TerminalLiveness(TerminalLivenessKind.EXITED),
        seed=b"",
    )

    assert session.pump_once() == b"hello\n\xff"
    assert session.pump_once() is None
    assert session.closed_code is TerminalWebSocketCloseCode.EXITED
    assert session.closed_reason == "terminal_exited"
    assert client.close_calls == 1


def test_slow_client_overflow_detaches_bridge_not_terminal():
    client = FakeClient(lines=[b"%output %1 12345\n"])
    record = _record()
    session = TerminalControlSession(
        record,
        client,
        liveness=lambda _: TerminalLiveness(TerminalLivenessKind.LIVE),
        seed=b"",
        queue=BoundedTerminalOutputQueue(
            max_frame_bytes=4,
            max_queue_bytes=4,
        ),
    )

    assert session.pump_once() is None
    assert session.closed_code is TerminalWebSocketCloseCode.INTERNAL_ERROR
    assert client.close_calls == 1


def test_control_eof_maps_live_dead_missing_and_unknown_liveness():
    expected = {
        TerminalLivenessKind.LIVE: TerminalWebSocketCloseCode.DETACHED,
        TerminalLivenessKind.EXITED: TerminalWebSocketCloseCode.EXITED,
        TerminalLivenessKind.MISSING: TerminalWebSocketCloseCode.NOT_FOUND,
        TerminalLivenessKind.UNKNOWN: TerminalWebSocketCloseCode.INTERNAL_ERROR,
    }
    for kind, close_code in expected.items():
        client = FakeClient(lines=[b""])
        session = TerminalControlSession(
            _record(),
            client,
            liveness=lambda _, observed=kind: TerminalLiveness(observed),
            seed=b"",
        )

        assert session.pump_once() is None
        assert session.closed_code is close_code


def test_subprocess_control_client_encodes_exact_hex_input_and_resize():
    process = FakeProcess()
    client = SubprocessTmuxControlClient(
        ("tmux", "-C", "attach-session"),
        pane_target="gl_deadbeef:0.0",
        session_target="gl_deadbeef",
        process_factory=lambda *args, **kwargs: process,
    )

    client.send_input(b"\x00A\xff")
    client.resize(rows=40, columns=120)
    client.close()

    assert process.stdin.getvalue().splitlines() == [
        b"send-keys -H -t gl_deadbeef:0.0 00 41 ff",
        b"resize-window -t gl_deadbeef:0 -x 120 -y 40",
        b"detach-client",
    ]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_control_mode_round_trips_non_utf8_bytes(tmp_path):
    socket_root = Path(tempfile.mkdtemp(prefix="gl-control-test-", dir="/tmp"))
    registry = ManagedTerminalRegistry()
    capability = probe_tmux()
    kernel = TmuxTerminalKernel(
        tmp_path,
        capability,
        socket_root=socket_root,
    )
    executable = Path("/bin/cat").resolve()
    spec = TmuxLaunchSpec(command=(str(executable),), cwd=tmp_path)
    identity = TerminalIdentity(
        owner_id="owner-real",
        workspace_id="workspace-real",
        session_id="session-real",
        terminal_name="terminal",
        session_key="session-key-real",
        native_harness_id="test-cli",
        command_digest=digest_terminal_command(spec.command),
        cwd_digest=digest_terminal_path(spec.cwd),
        executable_path_digest=digest_terminal_path(spec.command[0]),
        executable_version="test",
    )
    record = registry.ensure(
        identity,
        launch=lambda pending: kernel.launch(pending, spec),
    )
    session = TerminalControlBridge(
        registry,
        kernel,
        TerminalOriginPolicy((_ORIGIN,)),
    ).open(_request(record))
    session.next_frame()
    input_bytes = b"hello-\xff\n"
    expected_output = b"hello-\xff\r\n"
    output = bytearray()
    try:
        session.receive(input_bytes)
        for _ in range(30):
            frame = session.pump_once()
            if frame is not None:
                output.extend(frame)
            if expected_output in output:
                break
    finally:
        session.close()
        registry.close(
            record.id,
            identity.access,
            close_instance=kernel.close,
        )
        shutil.rmtree(socket_root, ignore_errors=True)

    assert expected_output in output


class FakeClient:
    def __init__(self, *, lines=()):
        self.lines = list(lines)
        self.inputs = []
        self.resizes = []
        self.close_calls = 0

    def read_line(self):
        return self.lines.pop(0) if self.lines else b""

    def send_input(self, data):
        self.inputs.append(data)

    def resize(self, *, rows, columns):
        self.resizes.append((rows, columns))

    def close(self):
        self.close_calls += 1


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.client = FakeClient()

    def capture_seed(self, terminal_id):
        self.calls.append(("capture", terminal_id))
        return b"seed"

    def open_control_mode(self, terminal_id):
        self.calls.append(("open", terminal_id))
        return self.client

    def liveness(self, terminal_id):
        self.calls.append(("liveness", terminal_id))
        return TerminalLiveness(TerminalLivenessKind.LIVE)


class FakeProcess:
    def __init__(self):
        self.stdin = NonClosingBytesIO()
        self.stdout = io.BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        del timeout
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class NonClosingBytesIO(io.BytesIO):
    def close(self):
        return None


def _registry():
    timestamps = (
        datetime(2026, 7, 30, 9, tzinfo=timezone.utc) + timedelta(seconds=offset)
        for offset in range(10)
    )
    registry = ManagedTerminalRegistry(
        now=lambda: next(timestamps),
        id_factory=lambda: "term_control",
    )
    record = registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)
    return registry, record


def _identity():
    return TerminalIdentity(
        owner_id="owner-1",
        workspace_id="workspace-1",
        session_id="session-1",
        terminal_name="codex",
        session_key="session-key-1",
        native_harness_id="codex-cli",
        command_digest=_DIGEST,
        cwd_digest=_DIGEST,
        executable_path_digest=_DIGEST,
        executable_version="test",
    )


def _record():
    _, record = _registry()
    return record


def _request(record, **changes):
    values = {
        "terminal_id": record.id,
        "owner_id": record.identity.owner_id,
        "workspace_id": record.identity.workspace_id,
        "session_id": record.identity.session_id,
        "revision": record.revision,
        "origin": _ORIGIN,
    }
    values.update(changes)
    return TerminalAttachRequest(**values)
