"""Hostile wire, lifecycle, process ownership, and isolation conformance."""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from gigaloom.harnesses.acp import (
    AcpLimits,
    AcpRouteIdentity,
    begin_prompt,
    create_acp_client,
    new_session,
    pin_acp_process,
)
from gigaloom.harnesses.acp.errors import AcpRequestCancelled
from gigaloom.harnesses.acp.conformance import run_non_persisting_probe
from gigaloom.harnesses.acp.process import AcpStdioTransport
from gigaloom.structured_processes import StructuredProcessError, StructuredProcessLost


_DIGEST = "d" * 64
_ROUTE = AcpRouteIdentity("fake-read-only", "fake.acp", _DIGEST)
_FIXTURE = Path(__file__).parents[2] / "fixtures" / "acp" / "fake_agent.py"


def _spec(tmp_path: Path, mode: str = "normal", *extra: str):
    return pin_acp_process(
        (_FIXTURE.resolve().as_posix(), "--mode", mode, *extra),
        cwd=tmp_path,
        environment={"PATH": os.environ["PATH"], "HOME": "/must/not/inherit"},
        allowed_environment=frozenset({"HOME"}),
    )


def _client(tmp_path: Path, mode: str = "normal", *extra: str):
    client = create_acp_client(
        _spec(tmp_path, mode, *extra),
        compatibility_profile_digest=_DIGEST,
        route_identity=_ROUTE,
        limits=AcpLimits(request_timeout_seconds=3.0),
    )
    client.start()
    return client


def test_fake_agent_passes_read_only_lifecycle_without_provider(tmp_path: Path) -> None:
    client = _client(tmp_path)
    snapshot = client.initialize()
    binding = new_session(client, workspace=tmp_path)
    result = begin_prompt(client, binding, text="read only").result(1.0)
    events = [client.supervisor.next_event(timeout=0.2) for _ in range(3)]
    client.close()

    assert snapshot.agent_info is not None
    assert snapshot.agent_info.name == "fake-read-only"
    assert result.stop_reason == "end_turn"
    assert result.usage is not None and result.usage.total_tokens == 3
    assert any(event and event.type == "tool.started" for event in events)


@pytest.mark.parametrize("mode", ["banner", "malformed", "oversized", "wrong-id"])
def test_hostile_framing_is_terminal(tmp_path: Path, mode: str) -> None:
    client = _client(tmp_path, mode)
    with pytest.raises(StructuredProcessLost):
        client.initialize()
    event_types = {
        event.type
        for _ in range(4)
        if (event := client.supervisor.next_event(timeout=0.1)) is not None
    }
    assert "protocol_error" in event_types
    assert client.supervisor.state.value == "lost"
    client.close()


def test_event_stream_resnapshots_with_bounded_occupancy(tmp_path: Path) -> None:
    client = _client(tmp_path, "stream")
    try:
        client.initialize()
        # The session/new response is ordered after the fixture's stream flood, so
        # it is a deterministic reader barrier rather than a producer-side sentinel.
        new_session(client, workspace=tmp_path)
        occupancy = client.supervisor.event_queue_occupancy
        assert 0 < occupancy <= 256
        events = [client.supervisor.next_event(timeout=0.0) for _ in range(occupancy)]
        assert any(event and event.type == "resnapshot_required" for event in events)
    finally:
        client.close()


def test_ignored_cancel_still_frees_local_waiter(tmp_path: Path) -> None:
    client = _client(tmp_path, "ignore-cancel")
    client.initialize()
    binding = new_session(client, workspace=tmp_path)
    handle = begin_prompt(client, binding, text="wait")
    started = time.monotonic()
    assert handle.cancel() is True
    with pytest.raises(AcpRequestCancelled):
        handle.result(0.2)
    assert time.monotonic() - started < 0.2
    client.close()


def test_outstanding_requests_have_a_hard_capacity(tmp_path: Path) -> None:
    client = _client(tmp_path, "ignore-cancel")
    client.initialize()
    binding = new_session(client, workspace=tmp_path)
    handles = [
        begin_prompt(client, binding, text=f"pending {index}") for index in range(64)
    ]
    assert client.supervisor.pending_request_count == 64
    with pytest.raises(StructuredProcessError, match="capacity"):
        begin_prompt(client, binding, text="one too many")
    assert len(handles) == 64
    client.close()


def test_stderr_is_bounded_and_close_kills_process_group(tmp_path: Path) -> None:
    limits = AcpLimits(max_stderr_bytes=1024, request_timeout_seconds=1.0)
    stderr_spec = _spec(tmp_path, "stderr-flood")
    transport = AcpStdioTransport(
        command=stderr_spec.command,
        runtime_id="acp-stderr-test",
        env=stderr_spec.env,
        cwd=stderr_spec.cwd,
        read_queue_size=limits.max_inbound_messages,
        max_stderr_bytes=limits.max_stderr_bytes,
    )
    client = create_acp_client(
        stderr_spec,
        compatibility_profile_digest=_DIGEST,
        route_identity=_ROUTE,
        limits=limits,
        transport_factory=lambda: transport,
    )
    client.start()
    client.initialize()
    deadline = time.monotonic() + 1.0
    while transport.stderr_byte_count < 1024 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport.stderr_byte_count == 1024
    client.close()

    pid_file = tmp_path / "child.pid"
    fork_client = _client(tmp_path, "fork", "--child-pid-file", pid_file.as_posix())
    fork_client.initialize()
    deadline = time.monotonic() + 1.0
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    fork_client.close()
    deadline = time.monotonic() + 2.0
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _process_exists(child_pid)


def test_probe_is_initialize_only_and_requires_network_isolation(
    tmp_path: Path,
) -> None:
    del tmp_path
    with pytest.raises(ValueError, match="network isolation"):
        run_non_persisting_probe(
            (_FIXTURE.resolve().as_posix(),),
            route_identity=_ROUTE,
            network_isolated=False,
        )
    receipt = run_non_persisting_probe(
        (_FIXTURE.resolve().as_posix(), "--mode", "home-write"),
        route_identity=_ROUTE,
        network_isolated=True,
    )
    assert receipt.state == "ready"
    assert receipt.session_created is False
    assert receipt.prompt_sent is False
    assert receipt.native_home_isolated is True
    assert receipt.network_policy == "enforced_deny"
    assert len(receipt.receipt_digest) == 64


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_acp_modules_are_bounded_and_contain_no_remote_client(tmp_path: Path) -> None:
    del tmp_path
    package = Path(__file__).parents[3] / "src" / "gigaloom" / "harnesses" / "acp"
    forbidden = ("import requests", "import httpx", "urllib.request", "aiohttp")
    for module in package.glob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 600, module.name
        assert not any(token in source for token in forbidden), module.name
