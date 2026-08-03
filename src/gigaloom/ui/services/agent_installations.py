from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
import re
import threading
from typing import Mapping, cast
from urllib.parse import urlencode
from uuid import uuid4

from gigaloom.contracts import AgentActivationV1
from gigaloom.contracts.operational_validation import validate_identity
from gigaloom.harnesses.agent_profiles.installations import (
    AgentInstallCancelled,
    AgentRuntimeService,
    InstallCancellationToken,
    InstallPlanningResult,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    read_json,
)
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAcpProbeReceipt
from gigaloom.harnesses.agent_profiles.onboarding import ManagedAgentOnboardingResult


MAX_INSTALLATION_OPERATIONS, MAX_OPERATION_EVENTS = 256, 64
_OPERATION_ID_RE = re.compile(r"agent-op-[0-9a-f]{32}\Z")
TERMINAL_OPERATION_STATES = frozenset(
    "completed inactive canceled failed recovered".split()
)


@dataclass(frozen=True, slots=True)
class AgentInstallationWebEvent:
    sequence: int
    state: str
    reason_code: str
    observed_at: str


@dataclass(frozen=True, slots=True)
class AgentInstallationWebOperation:
    operation_id: str
    kind: str
    registry_or_local_id: str
    requested_local_agent_id: str | None
    status: str
    events: tuple[AgentInstallationWebEvent, ...]
    result_local_agent_id: str | None
    result_install_id: str | None
    result_version: str | None
    result_active: bool | None
    terminal_reason_code: str | None
    content_free: bool = True

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_OPERATION_STATES


@dataclass(frozen=True, slots=True)
class AgentInstallationRecovery:
    recovered_operation_ids: tuple[str, ...]
    recovered_plan_ids: tuple[str, ...]
    cleanup_statuses: tuple[str, ...]


@dataclass(slots=True)
class _OperationState:
    operation_id: str
    kind: str
    registry_or_local_id: str
    requested_local_agent_id: str | None
    allow_unverified: bool
    status: str
    events: list[AgentInstallationWebEvent] = field(default_factory=list)
    result_local_agent_id: str | None = None
    result_install_id: str | None = None
    result_version: str | None = None
    result_active: bool | None = None
    terminal_reason_code: str | None = None
    token: InstallCancellationToken = field(default_factory=InstallCancellationToken)


class AgentInstallationWebService:
    """Run server-owned install decisions and expose monotonic operation events."""

    def __init__(
        self,
        data_root: str | Path,
        runtime: AgentRuntimeService,
        *,
        clock: Callable[[], datetime] | None = None,
        submit: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._root = (
            Path(data_root).expanduser().resolve(strict=False)
            / "agent_profiles/web_operations"
        )
        self._runtime = runtime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._submit = submit or _submit_daemon
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._operations = self._load_operations()

    def preview(
        self,
        registry_query: str,
        *,
        local_agent_id: str | None = None,
    ) -> InstallPlanningResult:
        result = self._runtime.add(
            registry_query,
            local_agent_id=local_agent_id,
            dry_run=True,
        )
        if not isinstance(result, InstallPlanningResult):
            raise RuntimeError("managed agent preview returned an install result")
        return result

    def start_install(
        self,
        registry_query: str,
        *,
        local_agent_id: str | None,
        expected_plan_id: str,
        confirmed: bool,
        allow_unverified: bool,
    ) -> AgentInstallationWebOperation:
        if not confirmed:
            raise ValueError("managed agent Web install requires confirmation")
        self._runtime.require_install_authority()
        operation = self._create(
            kind="install",
            registry_or_local_id=registry_query,
            requested_local_agent_id=local_agent_id,
            allow_unverified=allow_unverified,
        )
        self._submit(
            lambda: self._run_install(operation.operation_id, expected_plan_id)
        )
        return self.inspect_operation(operation.operation_id)

    def start_update(
        self,
        local_agent_id: str,
        *,
        confirmed: bool,
        allow_unverified: bool,
    ) -> AgentInstallationWebOperation:
        if not confirmed:
            raise ValueError("managed agent Web update requires confirmation")
        self._runtime.require_install_authority()
        operation = self._create(
            kind="update",
            registry_or_local_id=local_agent_id,
            requested_local_agent_id=local_agent_id,
            allow_unverified=allow_unverified,
        )
        self._submit(lambda: self._run_update(operation.operation_id))
        return self.inspect_operation(operation.operation_id)

    def inspect_operation(self, operation_id: str) -> AgentInstallationWebOperation:
        validate_identity(operation_id, field_name="agent installation operation id")
        with self._lock:
            try:
                return _snapshot(self._operations[operation_id])
            except KeyError as error:
                raise KeyError("unknown agent installation operation") from error

    def cancel_operation(self, operation_id: str) -> AgentInstallationWebOperation:
        validate_identity(operation_id, field_name="agent installation operation id")
        with self._lock:
            operation = self._get(operation_id)
            if _snapshot(operation).terminal:
                raise ValueError("agent installation operation is already terminal")
            operation.token.cancel()
            self._append_locked(
                operation,
                state="cancel_requested",
                reason_code="operator_requested_cancellation",
            )
            return _snapshot(operation)

    def wait_for_events(
        self,
        operation_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float = 1.0,
    ) -> tuple[tuple[AgentInstallationWebEvent, ...], bool]:
        if after_sequence < -1 or not 0 < timeout_seconds <= 5:
            raise ValueError("agent installation event cursor is invalid")
        with self._changed:
            operation = self._get(operation_id)
            events = tuple(
                item for item in operation.events if item.sequence > after_sequence
            )
            if not events and not _snapshot(operation).terminal:
                self._changed.wait(timeout_seconds)
                operation = self._get(operation_id)
                events = tuple(
                    item for item in operation.events if item.sequence > after_sequence
                )
            return events, _snapshot(operation).terminal

    def recover(self) -> AgentInstallationRecovery:
        staging = self._runtime.recover_abandoned()
        recovered = []
        with self._lock:
            for operation in self._operations.values():
                if operation.status != "recovery_required":
                    continue
                self._append_locked(
                    operation,
                    state="recovered",
                    reason_code="restart_recovery_completed",
                )
                operation.terminal_reason_code = "restart_recovery_completed"
                self._persist_locked(operation)
                recovered.append(operation.operation_id)
        return AgentInstallationRecovery(
            recovered_operation_ids=tuple(sorted(recovered)),
            recovered_plan_ids=tuple(item.plan_id for item in staging),
            cleanup_statuses=tuple(item.cleanup_status.value for item in staging),
        )

    def probe(self, local_agent_id: str) -> ManagedAcpProbeReceipt:
        return self._runtime.probe(local_agent_id)

    def activate(
        self, local_agent_id: str, install_id: str | None, *, confirmed: bool
    ) -> ManagedAgentOnboardingResult:
        return self._runtime.activate(local_agent_id, install_id, confirmed=confirmed)

    def rollback(self, local_agent_id: str, *, confirmed: bool) -> AgentActivationV1:
        if not confirmed:
            raise ValueError("managed agent Web rollback requires confirmation")
        return self._runtime.rollback(local_agent_id)

    def remove(self, local_agent_id: str, *, confirmed: bool) -> int:
        return self._runtime.remove(local_agent_id, confirmed=confirmed)

    def use_in_new_run(self, local_agent_id: str) -> tuple[str, str]:
        record = self._runtime.inspect(local_agent_id)
        if not record.active:
            raise ValueError("managed agent revision is inactive")
        selected = record.artifact.local_agent_id
        return selected, f"/web/work?{urlencode({'agent': selected})}"

    def _create(
        self,
        *,
        kind: str,
        registry_or_local_id: str,
        requested_local_agent_id: str | None,
        allow_unverified: bool,
    ) -> AgentInstallationWebOperation:
        validate_identity(kind, field_name="agent installation operation kind")
        validate_identity(
            registry_or_local_id,
            field_name="agent installation operation target",
        )
        if requested_local_agent_id is not None:
            validate_identity(
                requested_local_agent_id,
                field_name="requested local agent id",
            )
        with self._lock:
            if len(self._operations) >= MAX_INSTALLATION_OPERATIONS:
                raise ValueError("agent installation operation count exceeds its bound")
            operation_id = f"agent-op-{uuid4().hex}"
            operation = _OperationState(
                operation_id=operation_id,
                kind=kind,
                registry_or_local_id=registry_or_local_id,
                requested_local_agent_id=requested_local_agent_id,
                allow_unverified=allow_unverified,
                status="queued",
            )
            self._operations[operation_id] = operation
            self._append_locked(
                operation,
                state="queued",
                reason_code=f"{kind}_queued",
            )
            return _snapshot(operation)

    def _run_install(self, operation_id: str, expected_plan_id: str) -> None:
        operation = self._get(operation_id)
        try:
            result = self._runtime.add(
                operation.registry_or_local_id,
                local_agent_id=operation.requested_local_agent_id,
                confirmed=True,
                allow_unverified=operation.allow_unverified,
                expected_plan_id=expected_plan_id,
                cancellation=operation.token,
                progress=lambda state, reason: self._progress(
                    operation_id, state, reason
                ),
            )
            if isinstance(result, InstallPlanningResult):
                raise RuntimeError("managed agent operation did not install")
            self._complete(operation_id, result)
        except AgentInstallCancelled:
            self._terminal(
                operation_id,
                state="canceled",
                reason_code="managed_agent_install_cancelled",
            )
        except Exception as error:  # operation boundary records only a reason code
            self._terminal(
                operation_id,
                state="failed",
                reason_code=_failure_reason(error),
            )

    def _run_update(self, operation_id: str) -> None:
        operation = self._get(operation_id)
        try:
            result = self._runtime.update(
                operation.registry_or_local_id,
                confirmed=True,
                allow_unverified=operation.allow_unverified,
                cancellation=operation.token,
                progress=lambda state, reason: self._progress(
                    operation_id, state, reason
                ),
            )
            self._complete(operation_id, result)
        except AgentInstallCancelled:
            self._terminal(
                operation_id,
                state="canceled",
                reason_code="managed_agent_update_cancelled",
            )
        except Exception as error:
            self._terminal(
                operation_id,
                state="failed",
                reason_code=_failure_reason(error),
            )

    def _progress(self, operation_id: str, state: str, reason_code: str) -> None:
        with self._lock:
            operation = self._get(operation_id)
            if operation.status in TERMINAL_OPERATION_STATES:
                return
            self._append_locked(operation, state=state, reason_code=reason_code)

    def _complete(
        self,
        operation_id: str,
        result: ManagedAgentOnboardingResult,
    ) -> None:
        with self._lock:
            operation = self._get(operation_id)
            operation.result_local_agent_id = result.artifact.local_agent_id
            operation.result_install_id = result.artifact.install_id
            operation.result_version = result.artifact.version
            operation.result_active = result.active
            state = "completed" if result.active else "inactive"
            reason = f"activation_{result.activation.status.value}"
            operation.terminal_reason_code = reason
            self._append_locked(operation, state=state, reason_code=reason)

    def _terminal(self, operation_id: str, *, state: str, reason_code: str) -> None:
        with self._lock:
            operation = self._get(operation_id)
            if operation.status in TERMINAL_OPERATION_STATES:
                return
            operation.terminal_reason_code = reason_code
            self._append_locked(operation, state=state, reason_code=reason_code)

    def _append_locked(
        self,
        operation: _OperationState,
        *,
        state: str,
        reason_code: str,
    ) -> None:
        validate_identity(state, field_name="agent installation event state")
        validate_identity(reason_code, field_name="agent installation event reason")
        if len(operation.events) >= MAX_OPERATION_EVENTS:
            raise ValueError(
                "agent installation operation event count exceeds its bound"
            )
        operation.status = state
        operation.events.append(
            AgentInstallationWebEvent(
                sequence=len(operation.events),
                state=state,
                reason_code=reason_code,
                observed_at=_timestamp(self._clock()),
            )
        )
        self._persist_locked(operation)
        self._changed.notify_all()

    def _persist_locked(self, operation: _OperationState) -> None:
        atomic_write_json(
            self._root / f"{operation.operation_id}.json",
            _operation_to_dict(operation),
        )

    def _get(self, operation_id: str) -> _OperationState:
        try:
            return self._operations[operation_id]
        except KeyError as error:
            raise KeyError("unknown agent installation operation") from error

    def _load_operations(self) -> dict[str, _OperationState]:
        if not self._root.exists():
            return {}
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("agent installation operation root is invalid")
        paths = tuple(sorted(self._root.glob("*.json")))
        if len(paths) > MAX_INSTALLATION_OPERATIONS:
            raise ValueError("agent installation operation count exceeds its bound")
        return {
            operation.operation_id: operation
            for path in paths
            for operation in (_operation_from_dict(read_json(path), path.stem),)
        }


def _submit_daemon(callback: Callable[[], None]) -> None:
    threading.Thread(target=callback, daemon=True).start()


def _snapshot(value: _OperationState) -> AgentInstallationWebOperation:
    return AgentInstallationWebOperation(
        operation_id=value.operation_id,
        kind=value.kind,
        registry_or_local_id=value.registry_or_local_id,
        requested_local_agent_id=value.requested_local_agent_id,
        status=value.status,
        events=tuple(value.events),
        result_local_agent_id=value.result_local_agent_id,
        result_install_id=value.result_install_id,
        result_version=value.result_version,
        result_active=value.result_active,
        terminal_reason_code=value.terminal_reason_code,
    )


def _operation_to_dict(value: _OperationState) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation_id": value.operation_id,
        "kind": value.kind,
        "registry_or_local_id": value.registry_or_local_id,
        "requested_local_agent_id": value.requested_local_agent_id,
        "allow_unverified": value.allow_unverified,
        "status": value.status,
        "events": [
            {
                "sequence": item.sequence,
                "state": item.state,
                "reason_code": item.reason_code,
                "observed_at": item.observed_at,
            }
            for item in value.events
        ],
        "result_local_agent_id": value.result_local_agent_id,
        "result_install_id": value.result_install_id,
        "result_version": value.result_version,
        "result_active": value.result_active,
        "terminal_reason_code": value.terminal_reason_code,
        "content_free": True,
    }


def _operation_from_dict(value: Mapping[str, object], file_id: str) -> _OperationState:
    expected = {
        "schema_version",
        "operation_id",
        "kind",
        "registry_or_local_id",
        "requested_local_agent_id",
        "allow_unverified",
        "status",
        "events",
        "result_local_agent_id",
        "result_install_id",
        "result_version",
        "result_active",
        "terminal_reason_code",
        "content_free",
    }
    if (
        set(value) != expected
        or value["schema_version"] != 1
        or value["content_free"] is not True
        or not isinstance(value["allow_unverified"], bool)
    ):
        raise ValueError("agent installation operation fields are invalid")
    events_raw = value["events"]
    if (
        not isinstance(events_raw, list)
        or not events_raw
        or len(events_raw) > MAX_OPERATION_EVENTS
    ):
        raise ValueError("agent installation operation events are invalid")
    events = [_event_from_dict(_mapping(item)) for item in events_raw]
    if [item.sequence for item in events] != list(range(len(events))):
        raise ValueError("agent installation operation event sequence is invalid")
    operation_id = _string(value["operation_id"])
    if operation_id != file_id or _OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise ValueError("agent installation operation id is invalid")
    status = _string(value["status"])
    if status != events[-1].state or (
        status not in TERMINAL_OPERATION_STATES and len(events) >= MAX_OPERATION_EVENTS
    ):
        raise ValueError("agent installation operation status is invalid")
    if status not in TERMINAL_OPERATION_STATES:
        status = "recovery_required"
        events.append(
            AgentInstallationWebEvent(
                sequence=len(events),
                state="recovery_required",
                reason_code="process_restarted_during_operation",
                observed_at=events[-1].observed_at,
            )
        )
    return _OperationState(
        operation_id=operation_id,
        kind=_string(value["kind"]),
        registry_or_local_id=_string(value["registry_or_local_id"]),
        requested_local_agent_id=_optional_string(value["requested_local_agent_id"]),
        allow_unverified=cast(bool, value["allow_unverified"]),
        status=status,
        events=events,
        result_local_agent_id=_optional_string(value["result_local_agent_id"]),
        result_install_id=_optional_string(value["result_install_id"]),
        result_version=_optional_string(value["result_version"]),
        result_active=_optional_boolean(value["result_active"]),
        terminal_reason_code=_optional_string(value["terminal_reason_code"]),
    )


def _event_from_dict(value: Mapping[str, object]) -> AgentInstallationWebEvent:
    if set(value) != {"sequence", "state", "reason_code", "observed_at"}:
        raise ValueError("agent installation operation event fields are invalid")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("agent installation operation event sequence is invalid")
    return AgentInstallationWebEvent(
        sequence=sequence,
        state=_identity(value["state"], "agent installation event state"),
        reason_code=_identity(value["reason_code"], "agent installation event reason"),
        observed_at=_string(value["observed_at"]),
    )


def _failure_reason(error: Exception) -> str:
    value = getattr(error, "reason_code", None)
    if isinstance(value, str):
        try:
            return validate_identity(value, field_name="installation failure reason")
        except ValueError:
            pass
    return f"{error.__class__.__name__.lower()}_during_agent_operation"


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("agent installation Web clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("agent installation operation event must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("agent installation operation value must be text")
    return value


def _identity(value: object, field_name: str) -> str:
    return validate_identity(_string(value), field_name=field_name)


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _optional_boolean(value: object) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError("agent installation operation value must be boolean")
