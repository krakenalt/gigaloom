from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

import pytest

from gigaloom.native.terminal import (
    ManagedTerminalRegistry,
    TerminalAccessDeniedError,
    TerminalCloseError,
    TerminalConflictError,
    TerminalIdentity,
    TerminalLaunchError,
    TerminalNotFoundError,
    TerminalState,
    terminal_record_to_dict,
)


_DIGEST = "a" * 64


def test_terminal_contract_is_content_free_and_closed():
    registry = _registry()
    identity = _identity()

    record = registry.ensure(identity, launch=lambda _: TerminalState.RUNNING)
    payload = terminal_record_to_dict(record)

    assert payload == {
        "id": "term_test_1",
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "terminal_name": "codex",
        "native_harness_id": "codex-cli",
        "native_session_id": None,
        "command_digest": _DIGEST,
        "cwd_digest": _DIGEST,
        "executable_path_digest": _DIGEST,
        "executable_version": "1.2.3",
        "state": "running",
        "revision": 2,
        "created_at": "2026-07-30T09:00:00+00:00",
        "updated_at": "2026-07-30T09:00:01+00:00",
        "last_attached_at": None,
        "last_liveness_at": None,
    }
    assert set(TerminalState) == {
        TerminalState.STARTING,
        TerminalState.RUNNING,
        TerminalState.ATTACHED,
        TerminalState.DETACHED,
        TerminalState.EXITED,
        TerminalState.FAILED,
        TerminalState.ORPHANED,
        TerminalState.CLOSING,
        TerminalState.CLOSED,
    }
    assert "socket" not in payload
    assert "output" not in payload
    assert "session_key" not in payload


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("owner_id", "", "owner id"),
        ("terminal_name", " codex", "terminal name"),
        ("session_key", "key\nother", "session key"),
        ("command_digest", "not-a-digest", "command digest"),
        ("native_session_id", "", "native session id"),
    ),
)
def test_terminal_identity_rejects_ambiguous_values(field, value, message):
    values = _identity_values()
    values[field] = value

    with pytest.raises(ValueError, match=message):
        TerminalIdentity(**values)


def test_registry_filters_and_authorizes_exact_binding():
    registry = _registry()
    first = registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)
    other = registry.ensure(
        _identity(
            owner_id="owner-2",
            session_key="session-key-2",
        ),
        launch=lambda _: TerminalState.DETACHED,
    )

    assert registry.list(_identity().access) == (first,)
    assert registry.get(first.id, _identity().access) == first
    with pytest.raises(TerminalAccessDeniedError):
        registry.get(first.id, other.identity.access)
    with pytest.raises(TerminalNotFoundError):
        registry.get("term_missing", _identity().access)


def test_ensure_serializes_one_launch_per_terminal_identity():
    launch_entered = threading.Event()
    release_launch = threading.Event()
    launch_count = 0
    launch_count_lock = threading.Lock()
    registry = _registry()
    identity = _identity()

    def launch(_):
        nonlocal launch_count
        with launch_count_lock:
            launch_count += 1
        launch_entered.set()
        assert release_launch.wait(timeout=2)
        return TerminalState.RUNNING

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(registry.ensure, identity, launch=launch) for _ in range(8)
        ]
        assert launch_entered.wait(timeout=2)
        release_launch.set()
        records = [future.result(timeout=2) for future in futures]

    assert launch_count == 1
    assert {record.id for record in records} == {"term_test_1"}
    assert {record.state for record in records} == {TerminalState.RUNNING}


def test_ensure_detects_same_key_with_changed_executable_evidence():
    registry = _registry()
    registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)

    with pytest.raises(TerminalConflictError, match="identity changed"):
        registry.ensure(
            _identity(executable_version="2.0.0"),
            launch=lambda _: TerminalState.RUNNING,
        )


def test_launch_failure_is_recorded_and_not_retried():
    registry = _registry()
    identity = _identity()
    calls = 0

    def fail(_):
        nonlocal calls
        calls += 1
        raise OSError("private backend detail")

    with pytest.raises(TerminalLaunchError) as raised:
        registry.ensure(identity, launch=fail)
    failed = registry.get("term_test_1", identity.access)
    retried = registry.ensure(identity, launch=lambda _: TerminalState.RUNNING)

    assert str(raised.value) == "managed terminal launch failed"
    assert calls == 1
    assert failed.state is TerminalState.FAILED
    assert retried == failed


def test_launch_and_close_callbacks_run_outside_registry_map_lock():
    registry = _registry()
    identity = _identity()

    def assert_map_available(_):
        completed = threading.Event()

        def read_registry():
            registry.list(identity.access)
            completed.set()

        thread = threading.Thread(target=read_registry)
        thread.start()
        assert completed.wait(timeout=2)
        thread.join(timeout=2)

    record = registry.ensure(
        identity,
        launch=lambda pending: assert_map_available(pending) or TerminalState.RUNNING,
    )
    closed = registry.close(
        record.id,
        identity.access,
        close_instance=assert_map_available,
    )

    assert closed.state is TerminalState.CLOSED


def test_transition_checks_revision_and_keeps_detach_distinct_from_exit():
    registry = _registry()
    running = registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)
    attached = registry.transition(
        running.id,
        running.identity.access,
        TerminalState.ATTACHED,
        expected_revision=running.revision,
    )
    detached = registry.transition(
        attached.id,
        attached.identity.access,
        TerminalState.DETACHED,
        expected_revision=attached.revision,
    )

    assert attached.last_attached_at == "2026-07-30T09:00:02+00:00"
    assert detached.state is TerminalState.DETACHED
    assert detached.state is not TerminalState.EXITED
    with pytest.raises(TerminalConflictError, match="revision changed"):
        registry.transition(
            detached.id,
            detached.identity.access,
            TerminalState.RUNNING,
            expected_revision=attached.revision,
        )
    with pytest.raises(TerminalConflictError, match="invalid terminal transition"):
        registry.transition(
            detached.id,
            detached.identity.access,
            TerminalState.STARTING,
        )


def test_close_is_idempotent_and_records_backend_failure():
    registry = _registry()
    identity = _identity()
    running = registry.ensure(identity, launch=lambda _: TerminalState.RUNNING)
    close_calls = 0

    def close(_):
        nonlocal close_calls
        close_calls += 1

    closed = registry.close(
        running.id,
        identity.access,
        close_instance=close,
    )
    closed_again = registry.close(
        running.id,
        identity.access,
        close_instance=close,
    )

    assert closed_again == closed
    assert close_calls == 1

    other = registry.ensure(
        _identity(session_key="session-key-2"),
        launch=lambda _: TerminalState.RUNNING,
    )
    with pytest.raises(TerminalCloseError) as raised:
        registry.close(
            other.id,
            identity.access,
            close_instance=lambda _: _raise(OSError("tmux secret detail")),
        )
    assert str(raised.value) == "managed terminal close failed"
    assert registry.get(other.id, identity.access).state is TerminalState.FAILED


def test_registry_requires_timezone_aware_clock():
    registry = ManagedTerminalRegistry(
        now=lambda: datetime(2026, 7, 30, 9),
        id_factory=lambda: "term_test",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        registry.ensure(_identity(), launch=lambda _: TerminalState.RUNNING)


def _identity(**changes):
    values = _identity_values()
    values.update(changes)
    return TerminalIdentity(**values)


def _identity_values():
    return {
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "session_id": "session-1",
        "terminal_name": "codex",
        "session_key": "session-key-1",
        "native_harness_id": "codex-cli",
        "command_digest": _DIGEST,
        "cwd_digest": _DIGEST,
        "executable_path_digest": _DIGEST,
        "executable_version": "1.2.3",
        "native_session_id": None,
    }


def _registry():
    timestamps = (
        datetime(2026, 7, 30, 9, tzinfo=timezone.utc) + timedelta(seconds=offset)
        for offset in range(100)
    )
    ids = (f"term_test_{index}" for index in range(1, 100))
    return ManagedTerminalRegistry(
        now=lambda: next(timestamps),
        id_factory=lambda: next(ids),
    )


def _raise(exc):
    raise exc
