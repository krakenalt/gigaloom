"""Private metadata persistence, restart reconciliation, and orphan reaping."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping, Protocol

from gigaloom.native.terminal.contracts import (
    TerminalIdentity,
    TerminalRecord,
    TerminalState,
)
from gigaloom.native.terminal.lifecycle import (
    TerminalLifecycleAction,
    TerminalLifecycleOutcome,
    TerminalLifecycleReceipt,
)
from gigaloom.native.terminal.liveness import (
    TerminalLiveness,
    TerminalLivenessKind,
)
from gigaloom.native.terminal.registry import ManagedTerminalRegistry


TERMINAL_METADATA_SCHEMA_VERSION = 1
MAX_TERMINAL_METADATA_BYTES = 64 * 1024
MAX_TERMINAL_METADATA_RECORDS = 1000
DEFAULT_REAP_LIMIT = 100


class TerminalRecoveryBackend(Protocol):
    """Backend observations and cleanup used during restart recovery."""

    def liveness(self, terminal_id: str) -> TerminalLiveness:
        """Return pane-aware liveness."""

    def client_count(self, terminal_id: str) -> int:
        """Return attached tmux client count."""

    def close(self, record: TerminalRecord) -> None:
        """Close only the exact private instance."""


class TerminalMetadataStore:
    """Persist content-free terminal bindings in private atomic JSON files."""

    def __init__(self, state_root: str | Path) -> None:
        self.root = (
            Path(state_root).expanduser().resolve() / "native-terminals" / "registry"
        )

    def save(self, record: TerminalRecord) -> None:
        """Atomically save one strict terminal metadata record."""
        self._ensure_private_root()
        payload = _record_to_storage_dict(record)
        encoded = (
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_TERMINAL_METADATA_BYTES:
            raise ValueError("terminal metadata record is too large")
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{_record_key(record.id)}.",
            dir=self.root,
        )
        temporary = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path(record.id))
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    def load_all(self) -> tuple[TerminalRecord, ...]:
        """Load a bounded deterministic snapshot of private metadata."""
        try:
            paths = sorted(self.root.glob("*.json"))
        except OSError as exc:
            raise ValueError("terminal metadata registry is unreadable") from exc
        if len(paths) > MAX_TERMINAL_METADATA_RECORDS:
            raise ValueError("terminal metadata registry is too large")
        records = []
        ids = set()
        keys = set()
        for path in paths:
            try:
                if (
                    path.is_symlink()
                    or path.stat().st_size > MAX_TERMINAL_METADATA_BYTES
                ):
                    raise ValueError("terminal metadata file is invalid")
                raw = path.read_bytes()
                payload = json.loads(raw)
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("terminal metadata file is invalid") from exc
            record = _record_from_storage_dict(payload)
            if path.name != f"{_record_key(record.id)}.json":
                raise ValueError("terminal metadata filename is invalid")
            if record.id in ids or record.identity.registry_key in keys:
                raise ValueError("terminal metadata identities are not unique")
            ids.add(record.id)
            keys.add(record.identity.registry_key)
            records.append(record)
        return tuple(sorted(records, key=lambda item: (item.created_at, item.id)))

    def _ensure_private_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        if stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise ValueError("terminal metadata directory mode is invalid")

    def _path(self, terminal_id: str) -> Path:
        return self.root / f"{_record_key(terminal_id)}.json"


class TerminalRecoveryManager:
    """Restore truthful terminal states and reap only verified owned records."""

    def __init__(
        self,
        registry: ManagedTerminalRegistry,
        store: TerminalMetadataStore,
        backend: TerminalRecoveryBackend,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.backend = backend
        self._now = now or (lambda: datetime.now(timezone.utc))

    def reconcile(self) -> tuple[TerminalLifecycleReceipt, ...]:
        """Restore all bounded metadata and project current backend truth."""
        receipts = []
        for stored in self.store.load_all():
            current = self.registry.restore(stored)
            target, reason, observed = self._reconciled_state(current)
            updated = (
                self.registry.observe_liveness(
                    current.id,
                    current.identity.access,
                    target,
                    expected_revision=current.revision,
                )
                if observed
                else current
            )
            self.store.save(updated)
            receipts.append(
                self._receipt(
                    updated,
                    action=TerminalLifecycleAction.RECONCILE,
                    previous_state=current.state,
                    outcome=_outcome_for_state(
                        updated.state,
                        unchanged=updated.state is current.state,
                    ),
                    reason=reason,
                )
            )
        return tuple(receipts)

    def reap_orphans(
        self,
        *,
        limit: int = DEFAULT_REAP_LIMIT,
    ) -> tuple[TerminalLifecycleReceipt, ...]:
        """Close at most `limit` metadata-bound orphan or failed instances."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("terminal reap limit is invalid")
        receipts = []
        candidates = (
            record
            for record in self.store.load_all()
            if record.state in {TerminalState.ORPHANED, TerminalState.FAILED}
        )
        for stored in candidates:
            if len(receipts) >= limit:
                break
            current = self.registry.restore(stored)
            try:
                closed = self.registry.close(
                    current.id,
                    current.identity.access,
                    close_instance=self.backend.close,
                    expected_revision=current.revision,
                )
                outcome = TerminalLifecycleOutcome.CLOSED
                reason = "verified_private_terminal_reaped"
            except Exception:
                closed = self.registry.get(current.id, current.identity.access)
                outcome = TerminalLifecycleOutcome.FAILED
                reason = "verified_private_terminal_reap_failed"
            self.store.save(closed)
            receipts.append(
                self._receipt(
                    closed,
                    action=TerminalLifecycleAction.REAP,
                    previous_state=current.state,
                    outcome=outcome,
                    reason=reason,
                )
            )
        return tuple(receipts)

    def _reconciled_state(
        self,
        record: TerminalRecord,
    ) -> tuple[TerminalState, str, bool]:
        if record.state in {
            TerminalState.CLOSED,
            TerminalState.EXITED,
            TerminalState.FAILED,
        }:
            return record.state, "terminal_state_is_terminal", False
        observed = self.backend.liveness(record.id)
        if record.state is TerminalState.CLOSING:
            if observed.kind in {
                TerminalLivenessKind.EXITED,
                TerminalLivenessKind.MISSING,
            }:
                return (
                    TerminalState.CLOSED,
                    "interrupted_close_is_complete",
                    True,
                )
            return (
                TerminalState.FAILED,
                "interrupted_close_requires_reaping",
                True,
            )
        if observed.kind is TerminalLivenessKind.LIVE:
            clients = self.backend.client_count(record.id)
            if clients > 0:
                return (
                    TerminalState.ATTACHED,
                    "live_terminal_has_attached_clients",
                    True,
                )
            return (
                TerminalState.DETACHED,
                "live_terminal_has_no_attached_clients",
                True,
            )
        if observed.kind is TerminalLivenessKind.EXITED:
            return TerminalState.EXITED, "terminal_pane_exited", True
        return (
            TerminalState.ORPHANED,
            "terminal_backend_is_missing_or_unknown",
            True,
        )

    def _receipt(
        self,
        record: TerminalRecord,
        *,
        action: TerminalLifecycleAction,
        previous_state: TerminalState,
        outcome: TerminalLifecycleOutcome,
        reason: str,
    ) -> TerminalLifecycleReceipt:
        timestamp = self._now()
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("terminal recovery clock must be timezone-aware")
        return TerminalLifecycleReceipt(
            terminal_id=record.id,
            action=action,
            outcome=outcome,
            previous_state=previous_state,
            current_state=record.state,
            revision=record.revision,
            created_at=timestamp.astimezone(timezone.utc).isoformat(),
            reason=reason,
        )


def _record_to_storage_dict(record: TerminalRecord) -> dict[str, Any]:
    identity = record.identity
    return {
        "schema_version": TERMINAL_METADATA_SCHEMA_VERSION,
        "id": record.id,
        "identity": {
            "owner_id": identity.owner_id,
            "workspace_id": identity.workspace_id,
            "session_id": identity.session_id,
            "terminal_name": identity.terminal_name,
            "session_key": identity.session_key,
            "native_harness_id": identity.native_harness_id,
            "command_digest": identity.command_digest,
            "cwd_digest": identity.cwd_digest,
            "executable_path_digest": identity.executable_path_digest,
            "executable_version": identity.executable_version,
            "native_session_id": identity.native_session_id,
        },
        "state": record.state.value,
        "revision": record.revision,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_attached_at": record.last_attached_at,
        "last_liveness_at": record.last_liveness_at,
    }


def _record_from_storage_dict(value: Any) -> TerminalRecord:
    if not isinstance(value, Mapping):
        raise ValueError("terminal metadata must be an object")
    expected = {
        "schema_version",
        "id",
        "identity",
        "state",
        "revision",
        "created_at",
        "updated_at",
        "last_attached_at",
        "last_liveness_at",
    }
    if set(value) != expected or value.get("schema_version") != 1:
        raise ValueError("terminal metadata fields are invalid")
    raw_identity = value.get("identity")
    if not isinstance(raw_identity, Mapping):
        raise ValueError("terminal metadata identity is invalid")
    identity_fields = {
        "owner_id",
        "workspace_id",
        "session_id",
        "terminal_name",
        "session_key",
        "native_harness_id",
        "command_digest",
        "cwd_digest",
        "executable_path_digest",
        "executable_version",
        "native_session_id",
    }
    if set(raw_identity) != identity_fields:
        raise ValueError("terminal metadata identity fields are invalid")
    try:
        identity = TerminalIdentity(
            owner_id=str(raw_identity["owner_id"]),
            workspace_id=str(raw_identity["workspace_id"]),
            session_id=str(raw_identity["session_id"]),
            terminal_name=str(raw_identity["terminal_name"]),
            session_key=str(raw_identity["session_key"]),
            native_harness_id=str(raw_identity["native_harness_id"]),
            command_digest=str(raw_identity["command_digest"]),
            cwd_digest=str(raw_identity["cwd_digest"]),
            executable_path_digest=str(raw_identity["executable_path_digest"]),
            executable_version=str(raw_identity["executable_version"]),
            native_session_id=_optional_text(raw_identity["native_session_id"]),
        )
        state = TerminalState(str(value["state"]))
        return TerminalRecord(
            id=str(value["id"]),
            identity=identity,
            state=state,
            revision=_required_int(value["revision"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            last_attached_at=_optional_text(value["last_attached_at"]),
            last_liveness_at=_optional_text(value["last_liveness_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("terminal metadata values are invalid") from exc


def _record_key(terminal_id: str) -> str:
    return hashlib.sha256(terminal_id.encode("utf-8")).hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("terminal metadata optional value is invalid")
    return value


def _required_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("terminal metadata integer value is invalid")
    return value


def _outcome_for_state(
    state: TerminalState,
    *,
    unchanged: bool,
) -> TerminalLifecycleOutcome:
    if unchanged:
        return TerminalLifecycleOutcome.UNCHANGED
    mapping = {
        TerminalState.DETACHED: TerminalLifecycleOutcome.DETACHED,
        TerminalState.ATTACHED: TerminalLifecycleOutcome.ATTACHED,
        TerminalState.EXITED: TerminalLifecycleOutcome.EXITED,
        TerminalState.ORPHANED: TerminalLifecycleOutcome.ORPHANED,
        TerminalState.FAILED: TerminalLifecycleOutcome.FAILED,
        TerminalState.CLOSED: TerminalLifecycleOutcome.CLOSED,
    }
    return mapping.get(state, TerminalLifecycleOutcome.UNCHANGED)
