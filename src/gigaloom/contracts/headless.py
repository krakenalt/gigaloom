"""Deterministic non-interactive invocation and JSONL event contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, IntEnum
from typing import Mapping, Protocol, runtime_checkable

from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    freeze_json_object,
    normalize_identities,
    validate_absolute_path,
    validate_digest,
    validate_identity,
    validate_relative_path,
    validate_schema_version,
    validate_timestamp,
)


HEADLESS_CONTRACT_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION
HEADLESS_EVENT_FORMAT = "jsonl-v1"
MAX_HEADLESS_TIMEOUT_SECONDS = 86_400
MAX_HEADLESS_SEQUENCE = 2**63 - 1


class HeadlessPromptSourceKind(str, Enum):
    """Exactly one explicit source of a headless prompt."""

    POSITIONAL = "positional"
    FILE = "file"
    STDIN = "stdin"
    HARBOR_INSTRUCTION = "harbor_instruction"


class HeadlessEventFormat(str, Enum):
    """Versioned stdout event framing."""

    JSONL_V1 = HEADLESS_EVENT_FORMAT


class HeadlessCapsuleMode(str, Enum):
    """Explicit terminal capsule behavior."""

    DISABLED = "disabled"
    REFERENCE = "reference"
    EXPORT = "export"


class HeadlessEventKind(str, Enum):
    """Required canonical headless event kinds."""

    RUN_STARTED = "run_started"
    AGENT_RESOLVED = "agent_resolved"
    ROUTE_OBSERVED = "route_observed"
    TURN_STARTED = "turn_started"
    TOOL_ACTIVITY = "tool_activity"
    APPROVAL_REQUIRED = "approval_required"
    USAGE = "usage"
    ARTIFACT = "artifact"
    WARNING = "warning"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELED = "run_canceled"

    @property
    def terminal(self) -> bool:
        """Return whether this kind closes the event stream."""
        return self in {
            HeadlessEventKind.RUN_SUCCEEDED,
            HeadlessEventKind.RUN_FAILED,
            HeadlessEventKind.RUN_CANCELED,
        }


class HeadlessExitCode(IntEnum):
    """Frozen process exit semantics for headless execution."""

    SUCCESS = 0
    USAGE_OR_ADMISSION = 2
    AUTHENTICATION_REQUIRED = 10
    POLICY_REFUSAL = 20
    AGENT_OR_TRANSPORT_FAILURE = 30
    CANCELED_OR_TIMEOUT = 40
    STATE_OR_INTEGRITY_FAILURE = 50
    INTERNAL_INVARIANT_FAILURE = 70


@dataclass(frozen=True, slots=True)
class HeadlessPromptSourceV1:
    """Content-free binding to exactly one prompt source."""

    kind: HeadlessPromptSourceKind
    content_digest: str
    reference: str | None = None
    schema_version: int = HEADLESS_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(
            self.schema_version, field_name="headless prompt source"
        )
        if not isinstance(self.kind, HeadlessPromptSourceKind):
            raise ValueError("headless prompt source kind is invalid")
        validate_digest(self.content_digest, field_name="headless prompt digest")
        if self.kind is HeadlessPromptSourceKind.FILE:
            if self.reference is None:
                raise ValueError("file prompt source requires a reference")
            validate_absolute_path(self.reference, field_name="prompt file")
        elif self.reference is not None:
            raise ValueError("non-file prompt source cannot contain a reference")


@dataclass(frozen=True, slots=True)
class HeadlessInvocationV1:
    """Validated non-interactive run admission inputs."""

    run_id: str
    agent_id: str
    route_id: str
    model_id: str
    workspace: str
    prompt_source: HeadlessPromptSourceV1
    result_dir: str
    event_format: HeadlessEventFormat
    timeout_seconds: int
    permission_profile: str
    network_profile: str
    capsule_mode: HeadlessCapsuleMode
    environment_contract_digest: str
    no_input: bool
    schema_version: int = HEADLESS_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="headless invocation")
        for value, label in (
            (self.run_id, "headless run id"),
            (self.agent_id, "headless agent id"),
            (self.route_id, "headless route id"),
            (self.model_id, "headless model id"),
            (self.permission_profile, "headless permission profile"),
            (self.network_profile, "headless network profile"),
        ):
            validate_identity(value, field_name=label)
        workspace = validate_absolute_path(
            self.workspace, field_name="headless workspace"
        )
        result_dir = validate_absolute_path(
            self.result_dir,
            field_name="headless result directory",
        )
        if workspace == result_dir:
            raise ValueError("workspace and result directory must be distinct")
        if not isinstance(self.prompt_source, HeadlessPromptSourceV1):
            raise ValueError("headless invocation requires one prompt source")
        if self.event_format is not HeadlessEventFormat.JSONL_V1:
            raise ValueError("headless event format is invalid")
        if (
            isinstance(self.timeout_seconds, bool)
            or not 1 <= self.timeout_seconds <= MAX_HEADLESS_TIMEOUT_SECONDS
        ):
            raise ValueError("headless timeout is invalid")
        if not isinstance(self.capsule_mode, HeadlessCapsuleMode):
            raise ValueError("headless capsule mode is invalid")
        validate_digest(
            self.environment_contract_digest,
            field_name="headless environment contract digest",
        )
        if self.no_input is not True:
            raise ValueError("headless invocation must prohibit interactive input")


@dataclass(frozen=True, slots=True)
class HeadlessEventV1:
    """One canonical JSONL event envelope."""

    sequence: int
    run_id: str
    timestamp: datetime
    kind: HeadlessEventKind
    payload: Mapping[str, object]
    content_capture: bool
    schema_version: int = HEADLESS_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="headless event")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or not 0 <= self.sequence <= MAX_HEADLESS_SEQUENCE
        ):
            raise ValueError("headless event sequence is invalid")
        validate_identity(self.run_id, field_name="headless run id")
        validate_timestamp(self.timestamp, field_name="headless event timestamp")
        if not isinstance(self.kind, HeadlessEventKind):
            raise ValueError("headless event kind is invalid")
        if not isinstance(self.content_capture, bool):
            raise ValueError("headless content_capture must be boolean")
        payload = freeze_json_object(self.payload, field_name="headless event payload")
        object.__setattr__(self, "payload", payload)
        if self.kind.terminal:
            _validate_terminal_payload(payload)


@dataclass(frozen=True, slots=True)
class HeadlessTerminalReceiptV1:
    """Terminal stream evidence required in addition to the process exit code."""

    receipt_id: str
    run_id: str
    terminal_kind: HeadlessEventKind
    final_sequence: int
    exit_code: HeadlessExitCode
    result_ref: str | None
    capsule_ref: str | None
    omissions: tuple[str, ...]
    finished_at: datetime
    content_free: bool = True
    schema_version: int = HEADLESS_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="headless receipt")
        validate_identity(self.receipt_id, field_name="headless receipt id")
        validate_identity(self.run_id, field_name="headless run id")
        if not isinstance(self.terminal_kind, HeadlessEventKind) or not (
            self.terminal_kind.terminal
        ):
            raise ValueError("headless receipt requires a terminal event kind")
        if (
            isinstance(self.final_sequence, bool)
            or not isinstance(self.final_sequence, int)
            or not 0 <= self.final_sequence <= MAX_HEADLESS_SEQUENCE
        ):
            raise ValueError("headless final sequence is invalid")
        if not isinstance(self.exit_code, HeadlessExitCode):
            raise ValueError("headless exit code is invalid")
        if self.terminal_kind is HeadlessEventKind.RUN_SUCCEEDED:
            if self.exit_code is not HeadlessExitCode.SUCCESS:
                raise ValueError("successful terminal receipt requires exit code 0")
        elif self.exit_code is HeadlessExitCode.SUCCESS:
            raise ValueError("non-success terminal receipt cannot use exit code 0")
        if self.terminal_kind is HeadlessEventKind.RUN_CANCELED and (
            self.exit_code is not HeadlessExitCode.CANCELED_OR_TIMEOUT
        ):
            raise ValueError("canceled terminal receipt requires exit code 40")
        if self.result_ref is None and self.capsule_ref is None:
            raise ValueError("terminal receipt requires a result or capsule reference")
        if self.result_ref is not None:
            validate_relative_path(
                self.result_ref, field_name="headless result reference"
            )
        if self.capsule_ref is not None:
            validate_relative_path(
                self.capsule_ref,
                field_name="headless capsule reference",
            )
        omissions = normalize_identities(
            self.omissions,
            field_name="headless omissions",
        )
        object.__setattr__(self, "omissions", omissions)
        validate_timestamp(self.finished_at, field_name="headless finish time")
        if self.content_free is not True:
            raise ValueError("headless terminal receipt must be content-free")


@runtime_checkable
class HeadlessEventSinkPort(Protocol):
    """Public sink for ordered canonical events and exactly one close receipt."""

    def emit(self, event: HeadlessEventV1) -> None:
        """Append one event in monotonically increasing sequence order."""

    def close(self, receipt: HeadlessTerminalReceiptV1) -> None:
        """Close the stream exactly once with terminal evidence."""


def _validate_terminal_payload(payload: Mapping[str, object]) -> None:
    omissions = payload.get("omissions")
    if not isinstance(omissions, tuple) or any(
        not isinstance(item, str) for item in omissions
    ):
        raise ValueError("terminal headless event requires an omission list")
    result_ref = payload.get("result_ref")
    capsule_ref = payload.get("capsule_ref")
    if result_ref is None and capsule_ref is None:
        raise ValueError(
            "terminal headless event requires a result or capsule reference"
        )
    for value, label in (
        (result_ref, "terminal result reference"),
        (capsule_ref, "terminal capsule reference"),
    ):
        if value is not None:
            validate_relative_path(value, field_name=label)
