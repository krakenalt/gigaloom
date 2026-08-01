"""Application contracts for deterministic headless execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Mapping, Protocol, runtime_checkable

from gigaloom.contracts import (
    HeadlessEventKind,
    HeadlessExitCode,
    HeadlessInvocationV1,
)
from gigaloom.contracts.operational_validation import (
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_relative_path,
)


class HeadlessBackendStatus(str, Enum):
    """Typed terminal outcomes returned by an execution owner."""

    SUCCEEDED = "succeeded"
    AUTHENTICATION_REQUIRED = "authentication_required"
    POLICY_REFUSED = "policy_refused"
    AGENT_OR_TRANSPORT_FAILED = "agent_or_transport_failed"
    CANCELED = "canceled"
    STATE_OR_INTEGRITY_FAILED = "state_or_integrity_failed"
    INTERNAL_INVARIANT_FAILED = "internal_invariant_failed"

    @property
    def exit_code(self) -> HeadlessExitCode:
        """Return the frozen process exit code for this outcome."""
        return {
            HeadlessBackendStatus.SUCCEEDED: HeadlessExitCode.SUCCESS,
            HeadlessBackendStatus.AUTHENTICATION_REQUIRED: (
                HeadlessExitCode.AUTHENTICATION_REQUIRED
            ),
            HeadlessBackendStatus.POLICY_REFUSED: HeadlessExitCode.POLICY_REFUSAL,
            HeadlessBackendStatus.AGENT_OR_TRANSPORT_FAILED: (
                HeadlessExitCode.AGENT_OR_TRANSPORT_FAILURE
            ),
            HeadlessBackendStatus.CANCELED: HeadlessExitCode.CANCELED_OR_TIMEOUT,
            HeadlessBackendStatus.STATE_OR_INTEGRITY_FAILED: (
                HeadlessExitCode.STATE_OR_INTEGRITY_FAILURE
            ),
            HeadlessBackendStatus.INTERNAL_INVARIANT_FAILED: (
                HeadlessExitCode.INTERNAL_INVARIANT_FAILURE
            ),
        }[self]

    @property
    def terminal_kind(self) -> HeadlessEventKind:
        """Return the canonical terminal event kind for this outcome."""
        if self is HeadlessBackendStatus.SUCCEEDED:
            return HeadlessEventKind.RUN_SUCCEEDED
        if self is HeadlessBackendStatus.CANCELED:
            return HeadlessEventKind.RUN_CANCELED
        return HeadlessEventKind.RUN_FAILED


@dataclass(frozen=True, slots=True)
class HeadlessRouteSelectionV1:
    """Exact route identity admitted by a composition-owned resolver."""

    agent_id: str
    route_id: str
    model_id: str
    observation_digest: str

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.agent_id, "headless selected agent id"),
            (self.route_id, "headless selected route id"),
            (self.model_id, "headless selected model id"),
        ):
            validate_identity(value, field_name=field_name)
        validate_digest(
            self.observation_digest,
            field_name="headless route observation digest",
        )


@dataclass(frozen=True, slots=True)
class PreparedHeadlessRun:
    """Admitted invocation plus non-serializable prompt content."""

    invocation: HeadlessInvocationV1
    route: HeadlessRouteSelectionV1
    prompt: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.invocation, HeadlessInvocationV1):
            raise ValueError("prepared headless invocation is invalid")
        if not isinstance(self.route, HeadlessRouteSelectionV1):
            raise ValueError("prepared headless route is invalid")
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError("prepared headless prompt is invalid")
        if (
            self.invocation.agent_id,
            self.invocation.route_id,
            self.invocation.model_id,
        ) != (self.route.agent_id, self.route.route_id, self.route.model_id):
            raise ValueError("prepared headless route does not match the invocation")
        digest = hashlib.sha256(self.prompt.encode("utf-8")).hexdigest()
        if digest != self.invocation.prompt_source.content_digest:
            raise ValueError("prepared headless prompt digest does not match")


@dataclass(frozen=True, slots=True)
class HeadlessExecutionRequest:
    """One admitted request handed to an execution authority."""

    prepared: PreparedHeadlessRun

    @property
    def invocation(self) -> HeadlessInvocationV1:
        """Return the public content-free invocation contract."""
        return self.prepared.invocation

    @property
    def prompt(self) -> str:
        """Return prompt content only at the final execution boundary."""
        return self.prepared.prompt


@dataclass(frozen=True, slots=True)
class HeadlessExecutionResult:
    """Bounded terminal result returned by an execution authority."""

    status: HeadlessBackendStatus
    result_ref: str | None
    capsule_ref: str | None
    omissions: tuple[str, ...] = ()
    diagnostic_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, HeadlessBackendStatus):
            raise ValueError("headless backend status is invalid")
        if (
            self.status is HeadlessBackendStatus.SUCCEEDED
            and self.result_ref is None
            and self.capsule_ref is None
        ):
            raise ValueError("successful headless backend result requires an artifact")
        if self.result_ref is not None:
            validate_relative_path(
                self.result_ref,
                field_name="headless backend result reference",
            )
        if self.capsule_ref is not None:
            validate_relative_path(
                self.capsule_ref,
                field_name="headless backend capsule reference",
            )
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="headless backend omissions",
            ),
        )
        if self.diagnostic_code is not None:
            validate_identity(
                self.diagnostic_code,
                field_name="headless backend diagnostic code",
            )


@dataclass(frozen=True, slots=True)
class HeadlessRunResult:
    """Complete synchronous result without stdout framing concerns."""

    prepared: PreparedHeadlessRun
    execution: HeadlessExecutionResult

    @property
    def exit_code(self) -> HeadlessExitCode:
        """Return the frozen exit code selected by the backend status."""
        return self.execution.status.exit_code


@runtime_checkable
class HeadlessRouteResolverPort(Protocol):
    """Resolve an agent request to one exact admitted route."""

    def resolve(
        self,
        *,
        agent_id: str,
        route_id: str | None,
        model_id: str | None,
    ) -> HeadlessRouteSelectionV1:
        """Return an exact route or raise a content-free admission error."""


@runtime_checkable
class HeadlessProgressSinkPort(Protocol):
    """Bounded progress events whose envelope remains runner-owned."""

    def emit(
        self,
        kind: HeadlessEventKind,
        payload: Mapping[str, object],
        *,
        content_capture: bool = False,
    ) -> None:
        """Emit one non-terminal event through runner-owned sequencing."""


@runtime_checkable
class HeadlessExecutionPort(Protocol):
    """Execute one fully admitted request without interactive input."""

    def execute(
        self,
        request: HeadlessExecutionRequest,
        *,
        cancel_event: object | None,
        event_sink: HeadlessProgressSinkPort,
    ) -> HeadlessExecutionResult:
        """Run the selected backend and return one typed terminal outcome."""


__all__ = [
    "HeadlessBackendStatus",
    "HeadlessExecutionPort",
    "HeadlessExecutionRequest",
    "HeadlessExecutionResult",
    "HeadlessProgressSinkPort",
    "HeadlessRouteResolverPort",
    "HeadlessRouteSelectionV1",
    "HeadlessRunResult",
    "PreparedHeadlessRun",
]
