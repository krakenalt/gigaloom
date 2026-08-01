"""Map GigaLoom headless evidence without claiming Harbor task success."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import PurePosixPath
from typing import Any, Mapping, cast

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessEventV1,
    HeadlessExitCode,
    headless_event_from_dict,
)


MAX_HARBOR_EVENT_PROBE_BYTES = 72 * 1024


class HarborResultMappingError(RuntimeError):
    """Content-free failure to validate headless process evidence."""


@dataclass(frozen=True, slots=True)
class HarborHeadlessObservationV1:
    """Process evidence projected into Harbor context, never verifier outcome."""

    run_id: str
    process_exit_code: HeadlessExitCode
    terminal_kind: HeadlessEventKind
    first_sequence: int
    final_sequence: int
    event_log_ref: str
    result_log_ref: str
    result_ref: str | None
    capsule_ref: str | None
    environment_contract_digest: str
    schema_version: int = 1

    def to_context_metadata(self) -> dict[str, object]:
        """Return bounded metadata that leaves reward and success to Harbor."""
        result_artifact_log_ref = _log_artifact_ref(
            self.result_log_ref,
            self.result_ref,
        )
        capsule_log_ref = _log_artifact_ref(
            self.result_log_ref,
            self.capsule_ref,
        )
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "process_exit_code": int(self.process_exit_code),
            "terminal_kind": self.terminal_kind.value,
            "first_sequence": self.first_sequence,
            "final_sequence": self.final_sequence,
            "event_log_ref": self.event_log_ref,
            "result_log_ref": self.result_log_ref,
            "result_ref": self.result_ref,
            "capsule_ref": self.capsule_ref,
            "result_artifact_log_ref": result_artifact_log_ref,
            "capsule_log_ref": capsule_log_ref,
            "environment_contract_digest": self.environment_contract_digest,
            "verifier_authority": "harbor",
            "task_success": None,
        }


def map_harbor_headless_result(
    *,
    run_id: str,
    process_exit_code: object,
    first_event_line: object,
    terminal_event_line: object,
    event_log_ref: str,
    result_log_ref: str,
    environment_contract_digest: str,
) -> HarborHeadlessObservationV1:
    """Validate first/last JSONL framing and bind only process-level evidence."""
    if isinstance(process_exit_code, bool) or not isinstance(process_exit_code, int):
        raise HarborResultMappingError("headless process exit code is unsupported")
    try:
        exit_code = HeadlessExitCode(process_exit_code)
    except (TypeError, ValueError) as error:
        raise HarborResultMappingError(
            "headless process exit code is unsupported"
        ) from error
    first = _event(first_event_line)
    terminal = _event(terminal_event_line)
    if (
        first.run_id != run_id
        or terminal.run_id != run_id
        or first.sequence != 0
        or first.kind is not HeadlessEventKind.RUN_STARTED
        or terminal.sequence < first.sequence
        or not terminal.kind.terminal
    ):
        raise HarborResultMappingError("headless event boundary is inconsistent")
    if terminal.kind is HeadlessEventKind.RUN_SUCCEEDED:
        if exit_code is not HeadlessExitCode.SUCCESS:
            raise HarborResultMappingError("headless success framing is inconsistent")
    elif exit_code is HeadlessExitCode.SUCCESS:
        raise HarborResultMappingError("headless failure framing is inconsistent")
    if terminal.kind is HeadlessEventKind.RUN_CANCELED and (
        exit_code is not HeadlessExitCode.CANCELED_OR_TIMEOUT
    ):
        raise HarborResultMappingError("headless cancellation framing is inconsistent")

    payload = cast(Mapping[str, Any], terminal.payload)
    result_ref = _optional_ref(payload.get("result_ref"))
    capsule_ref = _optional_ref(payload.get("capsule_ref"))
    return HarborHeadlessObservationV1(
        run_id=run_id,
        process_exit_code=exit_code,
        terminal_kind=terminal.kind,
        first_sequence=first.sequence,
        final_sequence=terminal.sequence,
        event_log_ref=event_log_ref,
        result_log_ref=result_log_ref,
        result_ref=result_ref,
        capsule_ref=capsule_ref,
        environment_contract_digest=environment_contract_digest,
    )


def _event(value: object) -> HeadlessEventV1:
    if not isinstance(value, str) or not value or "\x1b" in value:
        raise HarborResultMappingError("headless event probe is invalid")
    line = value[:-1] if value.endswith("\n") else value
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_HARBOR_EVENT_PROBE_BYTES or "\n" in line or "\r" in line:
        raise HarborResultMappingError("headless event probe violates bounds")
    try:
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("event must be an object")
        return headless_event_from_dict(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise HarborResultMappingError("headless event probe is malformed") from error


def _optional_ref(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HarborResultMappingError("headless artifact reference is invalid")
    return value


def _log_artifact_ref(root: str, reference: str | None) -> str | None:
    if reference is None:
        return None
    return (PurePosixPath(root) / reference).as_posix()


__all__ = [
    "MAX_HARBOR_EVENT_PROBE_BYTES",
    "HarborHeadlessObservationV1",
    "HarborResultMappingError",
    "map_harbor_headless_result",
]
