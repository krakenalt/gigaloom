from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
import stat

import pytest

from gigaloom.native.terminal import (
    LocalTerminalAttachService,
    ManagedTerminalRegistry,
    TerminalIdentity,
    TerminalLaunchError,
    TerminalLifecycleOutcome,
    TerminalLiveness,
    TerminalLivenessKind,
    TerminalMetadataStore,
    TerminalRecoveryManager,
    TerminalState,
    terminal_lifecycle_receipt_to_dict,
)
from gigaloom.native.terminal.registry import TerminalConflictError


_DIGEST = "a" * 64


def test_metadata_store_round_trips_strict_private_content_free_records(tmp_path):
    registry = _registry()
    record = registry.ensure(
        _identity("one"),
        launch=lambda _: TerminalState.DETACHED,
    )
    store = TerminalMetadataStore(tmp_path)

    store.save(record)
    restored = store.load_all()
    path = next(store.root.glob("*.json"))
    payload = json.loads(path.read_text())

    assert restored == (record,)
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "socket" not in json.dumps(payload)
    assert "output" not in json.dumps(payload)
    assert payload["identity"]["session_key"] == "session-key-one"

    payload["unexpected"] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="fields"):
        store.load_all()


def test_registry_restore_rejects_id_and_identity_collisions():
    registry = _registry()
    first = registry.ensure(
        _identity("one"),
        launch=lambda _: TerminalState.RUNNING,
    )
    other_registry = _registry()
    conflicting_id = other_registry.ensure(
        _identity("two"),
        launch=lambda _: TerminalState.RUNNING,
    )

    assert registry.restore(first) == first
    with pytest.raises(TerminalConflictError, match="id changed"):
        registry.restore(conflicting_id)


@pytest.mark.parametrize(
    ("observed", "returncode", "state", "outcome"),
    (
        (
            TerminalLiveness(TerminalLivenessKind.LIVE),
            0,
            TerminalState.DETACHED,
            TerminalLifecycleOutcome.DETACHED,
        ),
        (
            TerminalLiveness(TerminalLivenessKind.EXITED, exit_status=7),
            0,
            TerminalState.EXITED,
            TerminalLifecycleOutcome.EXITED,
        ),
        (
            TerminalLiveness(TerminalLivenessKind.MISSING),
            0,
            TerminalState.ORPHANED,
            TerminalLifecycleOutcome.ORPHANED,
        ),
        (
            TerminalLiveness(TerminalLivenessKind.LIVE),
            1,
            TerminalState.FAILED,
            TerminalLifecycleOutcome.FAILED,
        ),
    ),
)
def test_local_attach_distinguishes_detach_exit_missing_and_failure(
    tmp_path,
    observed,
    returncode,
    state,
    outcome,
):
    registry = _registry()
    record = registry.ensure(
        _identity("attach"),
        launch=lambda _: TerminalState.RUNNING,
    )
    store = TerminalMetadataStore(tmp_path)
    store.save(record)
    backend = FakeRecoveryBackend(
        observations={record.id: observed},
        attach_returncode=returncode,
    )
    service = LocalTerminalAttachService(registry, store, backend)

    updated, receipt = service.attach(
        record.id,
        record.identity.access,
        expected_revision=record.revision,
    )
    payload = terminal_lifecycle_receipt_to_dict(receipt)

    assert updated.state is state
    assert receipt.outcome is outcome
    assert store.load_all() == (updated,)
    assert backend.attached == [record.id]
    assert set(payload) == {
        "terminal_id",
        "action",
        "outcome",
        "previous_state",
        "current_state",
        "revision",
        "created_at",
        "reason",
    }
    assert "socket" not in json.dumps(payload)
    assert "output" not in json.dumps(payload)


def test_reconcile_restores_live_detached_exited_and_orphaned_states(tmp_path):
    source = _registry()
    attached = source.ensure(
        _identity("attached"),
        launch=lambda _: TerminalState.ORPHANED,
    )
    detached = source.ensure(
        _identity("detached"),
        launch=lambda _: TerminalState.RUNNING,
    )
    exited = source.ensure(
        _identity("exited"),
        launch=lambda _: TerminalState.RUNNING,
    )
    orphaned = source.ensure(
        _identity("orphaned"),
        launch=lambda _: TerminalState.DETACHED,
    )
    store = TerminalMetadataStore(tmp_path)
    for record in (attached, detached, exited, orphaned):
        store.save(record)
    backend = FakeRecoveryBackend(
        observations={
            attached.id: TerminalLiveness(TerminalLivenessKind.LIVE),
            detached.id: TerminalLiveness(TerminalLivenessKind.LIVE),
            exited.id: TerminalLiveness(
                TerminalLivenessKind.EXITED,
                exit_status=0,
            ),
            orphaned.id: TerminalLiveness(TerminalLivenessKind.MISSING),
        },
        clients={attached.id: 1, detached.id: 0},
    )
    restored = ManagedTerminalRegistry()
    manager = TerminalRecoveryManager(restored, store, backend)

    receipts = manager.reconcile()

    assert {record.id: record.state for record in store.load_all()} == {
        attached.id: TerminalState.ATTACHED,
        detached.id: TerminalState.DETACHED,
        exited.id: TerminalState.EXITED,
        orphaned.id: TerminalState.ORPHANED,
    }
    assert all(record.last_liveness_at is not None for record in store.load_all())
    assert {receipt.outcome for receipt in receipts} == {
        TerminalLifecycleOutcome.ATTACHED,
        TerminalLifecycleOutcome.DETACHED,
        TerminalLifecycleOutcome.EXITED,
        TerminalLifecycleOutcome.ORPHANED,
    }


def test_reconcile_finishes_missing_close_and_marks_live_close_failed(tmp_path):
    source = _registry()
    missing = source.ensure(
        _identity("missing-close"),
        launch=lambda _: TerminalState.RUNNING,
    )
    live = source.ensure(
        _identity("live-close"),
        launch=lambda _: TerminalState.RUNNING,
    )
    missing = source.transition(
        missing.id,
        missing.identity.access,
        TerminalState.CLOSING,
    )
    live = source.transition(
        live.id,
        live.identity.access,
        TerminalState.CLOSING,
    )
    store = TerminalMetadataStore(tmp_path)
    store.save(missing)
    store.save(live)
    backend = FakeRecoveryBackend(
        observations={
            missing.id: TerminalLiveness(TerminalLivenessKind.MISSING),
            live.id: TerminalLiveness(TerminalLivenessKind.LIVE),
        },
    )

    TerminalRecoveryManager(
        ManagedTerminalRegistry(),
        store,
        backend,
    ).reconcile()

    assert {record.id: record.state for record in store.load_all()} == {
        missing.id: TerminalState.CLOSED,
        live.id: TerminalState.FAILED,
    }


def test_reaper_is_bounded_and_only_closes_metadata_bound_candidates(tmp_path):
    source = _registry()
    first = source.ensure(
        _identity("first"),
        launch=lambda _: TerminalState.ORPHANED,
    )
    with pytest.raises(TerminalLaunchError):
        source.ensure(
            _identity("second"),
            launch=lambda _: _raise(OSError("launch failed")),
        )
    second = source.get("term_test_2", _identity("second").access)
    store = TerminalMetadataStore(tmp_path)
    store.save(first)
    store.save(second)
    unknown = tmp_path / "native-terminals" / "unknown-private-instance"
    unknown.mkdir()
    backend = FakeRecoveryBackend()
    manager = TerminalRecoveryManager(
        ManagedTerminalRegistry(),
        store,
        backend,
    )

    receipts = manager.reap_orphans(limit=1)

    assert len(receipts) == 1
    assert backend.closed == [first.id]
    assert unknown.is_dir()
    states = {record.id: record.state for record in store.load_all()}
    assert states[first.id] is TerminalState.CLOSED
    assert states[second.id] is TerminalState.FAILED


class FakeRecoveryBackend:
    def __init__(
        self,
        *,
        observations=None,
        clients=None,
        attach_returncode=0,
    ):
        self.observations = dict(observations or {})
        self.clients = dict(clients or {})
        self.attach_returncode = attach_returncode
        self.attached = []
        self.closed = []

    def liveness(self, terminal_id):
        return self.observations.get(
            terminal_id,
            TerminalLiveness(TerminalLivenessKind.MISSING),
        )

    def client_count(self, terminal_id):
        return self.clients.get(terminal_id, 0)

    def attach_local(self, terminal_id):
        self.attached.append(terminal_id)
        return self.attach_returncode

    def close(self, record):
        self.closed.append(record.id)


def _registry():
    counter = itertools.count(1)
    timestamps = itertools.count()
    base = datetime(2026, 7, 30, 9, tzinfo=timezone.utc)
    return ManagedTerminalRegistry(
        id_factory=lambda: f"term_test_{next(counter)}",
        now=lambda: base + timedelta(seconds=next(timestamps)),
    )


def _identity(suffix):
    digest = hashlib.sha256(suffix.encode()).hexdigest()
    return TerminalIdentity(
        owner_id="owner-1",
        workspace_id="workspace-1",
        session_id="session-1",
        terminal_name=f"terminal-{suffix}",
        session_key=f"session-key-{suffix}",
        native_harness_id="codex-cli",
        command_digest=digest or _DIGEST,
        cwd_digest=_DIGEST,
        executable_path_digest=_DIGEST,
        executable_version="1.2.3",
    )


def _raise(error):
    raise error
