"""Race-safe in-memory registry for managed native terminal instances."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import threading
from uuid import uuid4

from gigaloom.native.terminal.contracts import (
    TerminalAccess,
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
)


TerminalLauncher = Callable[[TerminalRecord], TerminalState]
TerminalCloser = Callable[[TerminalRecord], None]


class TerminalNotFoundError(KeyError):
    """Raised when a managed terminal id is not registered."""


class TerminalAccessDeniedError(PermissionError):
    """Raised before a terminal can be resolved across an authority boundary."""


class TerminalConflictError(RuntimeError):
    """Raised when a terminal identity or optimistic revision changed."""


class TerminalLaunchError(RuntimeError):
    """Raised after a managed terminal launch callback fails."""


class TerminalCloseError(RuntimeError):
    """Raised after a managed terminal close callback fails."""


_ALLOWED_TRANSITIONS: dict[TerminalState, frozenset[TerminalState]] = {
    TerminalState.STARTING: frozenset(
        {
            TerminalState.RUNNING,
            TerminalState.ATTACHED,
            TerminalState.DETACHED,
            TerminalState.EXITED,
            TerminalState.FAILED,
            TerminalState.ORPHANED,
            TerminalState.CLOSING,
        }
    ),
    TerminalState.RUNNING: frozenset(
        {
            TerminalState.ATTACHED,
            TerminalState.DETACHED,
            TerminalState.EXITED,
            TerminalState.FAILED,
            TerminalState.ORPHANED,
            TerminalState.CLOSING,
        }
    ),
    TerminalState.ATTACHED: frozenset(
        {
            TerminalState.RUNNING,
            TerminalState.DETACHED,
            TerminalState.EXITED,
            TerminalState.FAILED,
            TerminalState.ORPHANED,
            TerminalState.CLOSING,
        }
    ),
    TerminalState.DETACHED: frozenset(
        {
            TerminalState.RUNNING,
            TerminalState.ATTACHED,
            TerminalState.EXITED,
            TerminalState.FAILED,
            TerminalState.ORPHANED,
            TerminalState.CLOSING,
        }
    ),
    TerminalState.EXITED: frozenset({TerminalState.CLOSING, TerminalState.CLOSED}),
    TerminalState.FAILED: frozenset({TerminalState.CLOSING, TerminalState.CLOSED}),
    TerminalState.ORPHANED: frozenset(
        {
            TerminalState.RUNNING,
            TerminalState.DETACHED,
            TerminalState.EXITED,
            TerminalState.FAILED,
            TerminalState.CLOSING,
        }
    ),
    TerminalState.CLOSING: frozenset({TerminalState.CLOSED, TerminalState.FAILED}),
    TerminalState.CLOSED: frozenset(),
}


class ManagedTerminalRegistry:
    """Coordinate terminal identities without holding the map lock during I/O."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"term_{uuid4().hex}")
        self._records: dict[str, TerminalRecord] = {}
        self._terminal_ids_by_key: dict[tuple[str, str, str, str, str], str] = {}
        self._instance_locks: dict[tuple[str, str, str, str, str], threading.RLock] = {}
        self._map_lock = threading.RLock()

    def ensure(
        self,
        identity: TerminalIdentity,
        *,
        launch: TerminalLauncher,
    ) -> TerminalRecord:
        """Launch one exact terminal identity at most once."""
        key = identity.registry_key
        instance_lock = self._instance_lock(key)
        with instance_lock:
            with self._map_lock:
                existing = self._record_for_key_unlocked(key)
                if existing is not None:
                    self._require_same_identity(existing, identity)
                    return existing
                timestamp = self._timestamp()
                pending = TerminalRecord(
                    id=self._id_factory(),
                    identity=identity,
                    state=TerminalState.STARTING,
                    revision=1,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                if pending.id in self._records:
                    raise TerminalConflictError("terminal id already exists")
                self._records[pending.id] = pending
                self._terminal_ids_by_key[key] = pending.id

            try:
                launched_state = launch(pending)
                if launched_state not in {
                    TerminalState.RUNNING,
                    TerminalState.ATTACHED,
                    TerminalState.DETACHED,
                    TerminalState.EXITED,
                    TerminalState.ORPHANED,
                }:
                    raise ValueError("terminal launcher returned an invalid state")
            except Exception as exc:
                with self._map_lock:
                    self._records[pending.id] = self._transition_unlocked(
                        self._records[pending.id],
                        TerminalState.FAILED,
                    )
                raise TerminalLaunchError("managed terminal launch failed") from exc

            with self._map_lock:
                launched = self._transition_unlocked(
                    self._records[pending.id],
                    launched_state,
                )
                self._records[pending.id] = launched
                return launched

    def list(self, access: TerminalAccess) -> tuple[TerminalRecord, ...]:
        """Return terminals in one exact owner/workspace/session scope."""
        with self._map_lock:
            records = (
                record
                for record in self._records.values()
                if _matches_access(record, access)
            )
            return tuple(sorted(records, key=lambda item: (item.created_at, item.id)))

    def get(
        self,
        terminal_id: str,
        access: TerminalAccess,
        *,
        expected_revision: int | None = None,
    ) -> TerminalRecord:
        """Resolve one terminal only after exact binding checks."""
        with self._map_lock:
            return self._resolve_unlocked(
                terminal_id,
                access,
                expected_revision=expected_revision,
            )

    def transition(
        self,
        terminal_id: str,
        access: TerminalAccess,
        state: TerminalState,
        *,
        expected_revision: int | None = None,
    ) -> TerminalRecord:
        """Apply one valid lifecycle transition under the per-instance lock."""
        instance_lock = self._lock_for_terminal(terminal_id)
        with instance_lock:
            with self._map_lock:
                current = self._resolve_unlocked(
                    terminal_id,
                    access,
                    expected_revision=expected_revision,
                )
                updated = self._transition_unlocked(current, state)
                self._records[terminal_id] = updated
                return updated

    def close(
        self,
        terminal_id: str,
        access: TerminalAccess,
        *,
        close_instance: TerminalCloser,
        expected_revision: int | None = None,
    ) -> TerminalRecord:
        """Close one terminal idempotently without map-locking backend I/O."""
        instance_lock = self._lock_for_terminal(terminal_id)
        with instance_lock:
            with self._map_lock:
                current = self._resolve_unlocked(
                    terminal_id,
                    access,
                    expected_revision=expected_revision,
                )
                if current.state is TerminalState.CLOSED:
                    return current
                closing = self._transition_unlocked(
                    current,
                    TerminalState.CLOSING,
                )
                self._records[terminal_id] = closing

            try:
                close_instance(closing)
            except Exception as exc:
                with self._map_lock:
                    self._records[terminal_id] = self._transition_unlocked(
                        self._records[terminal_id],
                        TerminalState.FAILED,
                    )
                raise TerminalCloseError("managed terminal close failed") from exc

            with self._map_lock:
                closed = self._transition_unlocked(
                    self._records[terminal_id],
                    TerminalState.CLOSED,
                )
                self._records[terminal_id] = closed
                return closed

    def _instance_lock(
        self,
        key: tuple[str, str, str, str, str],
    ) -> threading.RLock:
        with self._map_lock:
            return self._instance_locks.setdefault(key, threading.RLock())

    def _lock_for_terminal(self, terminal_id: str) -> threading.RLock:
        with self._map_lock:
            record = self._records.get(terminal_id)
            if record is None:
                raise TerminalNotFoundError(terminal_id)
            return self._instance_locks[record.identity.registry_key]

    def _record_for_key_unlocked(
        self,
        key: tuple[str, str, str, str, str],
    ) -> TerminalRecord | None:
        terminal_id = self._terminal_ids_by_key.get(key)
        return None if terminal_id is None else self._records[terminal_id]

    def _resolve_unlocked(
        self,
        terminal_id: str,
        access: TerminalAccess,
        *,
        expected_revision: int | None,
    ) -> TerminalRecord:
        record = self._records.get(terminal_id)
        if record is None:
            raise TerminalNotFoundError(terminal_id)
        if not _matches_access(record, access):
            raise TerminalAccessDeniedError("terminal binding does not match")
        if expected_revision is not None and record.revision != expected_revision:
            raise TerminalConflictError("terminal revision changed")
        return record

    def _transition_unlocked(
        self,
        record: TerminalRecord,
        state: TerminalState,
    ) -> TerminalRecord:
        if not isinstance(state, TerminalState):
            raise ValueError("terminal state is invalid")
        if state is record.state:
            return record
        if state not in _ALLOWED_TRANSITIONS[record.state]:
            raise TerminalConflictError(
                f"invalid terminal transition: {record.state.value} -> {state.value}"
            )
        timestamp = self._timestamp()
        return replace(
            record,
            state=state,
            revision=record.revision + 1,
            updated_at=timestamp,
            last_attached_at=(
                timestamp
                if state is TerminalState.ATTACHED
                else record.last_attached_at
            ),
        )

    @staticmethod
    def _require_same_identity(
        existing: TerminalRecord,
        identity: TerminalIdentity,
    ) -> None:
        if existing.identity != identity:
            raise TerminalConflictError("terminal ensure identity changed")

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("terminal registry clock must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()


def _matches_access(record: TerminalRecord, access: TerminalAccess) -> bool:
    return record.identity.access == access
