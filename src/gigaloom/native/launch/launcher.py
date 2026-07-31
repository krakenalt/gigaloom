"""One generic launcher for every registered native Agent Profile."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from gigaloom.native.launch.context import TerminalContext
from gigaloom.native.launch.contracts import (
    AgentResolutionKind,
    AgentResolutionResult,
    NativeAgentLaunchSpec,
    NativeInvocation,
    NativeLaunchMode,
)
from gigaloom.native.launch.executable import (
    NativeExecutableIdentity,
    NativeExecutableStatus,
    resolve_profile_executable,
    revalidate_executable_identity,
)
from gigaloom.native.launch.planner import NativeLaunchPlan, plan_native_launch


class RegisteredNativeLaunchStatus(str, Enum):
    """Typed preparation state with no provider content."""

    READY = "ready"
    NOT_AGENT = "not_agent"
    NO_NATIVE_ROUTE = "no_native_route"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    EXECUTABLE_MISSING = "executable_missing"
    EXECUTABLE_NON_EXECUTABLE = "executable_non_executable"
    EXECUTABLE_UNSAFE = "executable_unsafe"
    EXECUTABLE_DRIFTED = "executable_drifted"


NativeLaunchRunner = Callable[
    [NativeExecutableIdentity, tuple[str, ...], Mapping[str, str], str],
    int,
]


class NativeAgentProfile(Protocol):
    """Minimum harness-owned profile view consumed by native launch."""

    agent_id: str
    profile_digest: str
    native: NativeAgentLaunchSpec | None
    platform_support: tuple[str, ...]


class NativeAgentRegistry(Protocol):
    """Public resolution port; native launch does not own profile storage."""

    def resolve(self, requested_token: str) -> AgentResolutionResult:
        """Resolve one root token."""

    def get(self, agent_id: str) -> NativeAgentProfile:
        """Return one exact registered profile."""


@dataclass(frozen=True)
class PreparedRegisteredNativeLaunch:
    """Transient launch request; raw provider suffix is never serializable evidence."""

    agent_id: str
    profile_digest: str
    requested_token: str
    executable: NativeExecutableIdentity
    provider_suffix: tuple[str, ...] = field(repr=False)
    cwd: str
    environment: Mapping[str, str] = field(repr=False)
    plan: NativeLaunchPlan
    status: RegisteredNativeLaunchStatus = RegisteredNativeLaunchStatus.READY

    def __post_init__(self) -> None:
        if self.status is not RegisteredNativeLaunchStatus.READY:
            raise ValueError("prepared native launch must be ready")


@dataclass(frozen=True)
class RegisteredNativeLaunchFailure:
    """Content-free native preparation or drift failure."""

    status: RegisteredNativeLaunchStatus
    requested_token: str
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if self.status is RegisteredNativeLaunchStatus.READY:
            raise ValueError("native launch failure cannot be ready")


@dataclass(frozen=True)
class RegisteredNativeLaunchExecution:
    """Provider-owned exit status after one selected native runner."""

    agent_id: str
    mode: NativeLaunchMode
    exit_code: int


PreparedOrFailure = PreparedRegisteredNativeLaunch | RegisteredNativeLaunchFailure
ExecutionOrFailure = RegisteredNativeLaunchExecution | RegisteredNativeLaunchFailure


def prepare_registered_native_launch(
    argv: Sequence[str],
    *,
    registry: NativeAgentRegistry,
    context: TerminalContext,
    environment: Mapping[str, str] | None = None,
    facade_executable: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> PreparedOrFailure:
    """Resolve one registered profile into an exact native process plan."""
    if not argv:
        return RegisteredNativeLaunchFailure(
            RegisteredNativeLaunchStatus.NOT_AGENT,
            requested_token="",
        )
    requested_token = argv[0]
    resolution = registry.resolve(requested_token)
    if resolution.kind not in {
        AgentResolutionKind.AGENT_ID,
        AgentResolutionKind.AGENT_ALIAS,
    }:
        return RegisteredNativeLaunchFailure(
            RegisteredNativeLaunchStatus.NOT_AGENT,
            requested_token=requested_token,
        )
    assert resolution.agent_id is not None
    profile = registry.get(resolution.agent_id)
    if profile.native is None:
        return RegisteredNativeLaunchFailure(
            RegisteredNativeLaunchStatus.NO_NATIVE_ROUTE,
            requested_token=requested_token,
            agent_id=profile.agent_id,
        )
    if context.platform not in profile.platform_support:
        return RegisteredNativeLaunchFailure(
            RegisteredNativeLaunchStatus.UNSUPPORTED_PLATFORM,
            requested_token=requested_token,
            agent_id=profile.agent_id,
        )
    native_environment = dict(os.environ if environment is None else environment)
    executable = resolve_profile_executable(
        profile.native,
        environment=native_environment,
        facade_executable=facade_executable,
        platform=context.platform,
    )
    if executable.status is not NativeExecutableStatus.READY:
        return RegisteredNativeLaunchFailure(
            _status_for_executable(executable.status),
            requested_token=requested_token,
            agent_id=profile.agent_id,
        )
    assert executable.identity is not None
    launch_cwd = os.fspath(Path.cwd() if cwd is None else cwd)
    provider_suffix = tuple(argv[1:])
    invocation = NativeInvocation(
        agent_id=profile.agent_id,
        requested_token=requested_token,
        suffix=provider_suffix,
        cwd=launch_cwd,
        stdin_is_tty=context.stdin_is_tty,
        stdout_is_tty=context.stdout_is_tty,
        stderr_is_tty=context.stderr_is_tty,
        ci=context.ci,
        platform=context.platform,
    )
    plan = plan_native_launch(
        invocation,
        profile.native,
        managed_terminal_supported=context.terminal_supported,
    )
    return PreparedRegisteredNativeLaunch(
        agent_id=profile.agent_id,
        profile_digest=profile.profile_digest,
        requested_token=requested_token,
        executable=executable.identity,
        provider_suffix=provider_suffix,
        cwd=launch_cwd,
        environment=MappingProxyType(native_environment),
        plan=plan,
    )


def execute_prepared_native_launch(
    prepared: PreparedRegisteredNativeLaunch,
    *,
    direct_runner: NativeLaunchRunner,
    managed_runner: NativeLaunchRunner,
) -> ExecutionOrFailure:
    """Revalidate the pinned file and invoke exactly one selected runner."""
    if revalidate_executable_identity(prepared.executable) is not (
        NativeExecutableStatus.READY
    ):
        return RegisteredNativeLaunchFailure(
            RegisteredNativeLaunchStatus.EXECUTABLE_DRIFTED,
            requested_token=prepared.requested_token,
            agent_id=prepared.agent_id,
        )
    runner = (
        managed_runner
        if prepared.plan.mode is NativeLaunchMode.MANAGED_NATIVE
        else direct_runner
    )
    exit_code = runner(
        prepared.executable,
        prepared.provider_suffix,
        prepared.environment,
        prepared.cwd,
    )
    return RegisteredNativeLaunchExecution(
        agent_id=prepared.agent_id,
        mode=prepared.plan.mode,
        exit_code=exit_code,
    )


def launch_registered_native_agent(
    argv: Sequence[str],
    *,
    registry: NativeAgentRegistry,
    context: TerminalContext,
    direct_runner: NativeLaunchRunner,
    managed_runner: NativeLaunchRunner,
    environment: Mapping[str, str] | None = None,
    facade_executable: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> ExecutionOrFailure:
    """Prepare and run any registered native agent without provider branching."""
    prepared = prepare_registered_native_launch(
        argv,
        registry=registry,
        context=context,
        environment=environment,
        facade_executable=facade_executable,
        cwd=cwd,
    )
    if isinstance(prepared, RegisteredNativeLaunchFailure):
        return prepared
    return execute_prepared_native_launch(
        prepared,
        direct_runner=direct_runner,
        managed_runner=managed_runner,
    )


def _status_for_executable(
    status: NativeExecutableStatus,
) -> RegisteredNativeLaunchStatus:
    return {
        NativeExecutableStatus.MISSING: (
            RegisteredNativeLaunchStatus.EXECUTABLE_MISSING
        ),
        NativeExecutableStatus.NON_EXECUTABLE: (
            RegisteredNativeLaunchStatus.EXECUTABLE_NON_EXECUTABLE
        ),
        NativeExecutableStatus.UNSAFE: (RegisteredNativeLaunchStatus.EXECUTABLE_UNSAFE),
        NativeExecutableStatus.DRIFTED: (
            RegisteredNativeLaunchStatus.EXECUTABLE_DRIFTED
        ),
    }[status]
