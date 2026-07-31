"""Execution-free compatibility probe planning for Agent Profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import shutil

from gigaloom.harnesses.agent_profiles.models import AgentProfileV1


class AgentProbePlanStatus(str, Enum):
    """Content-free readiness for a later explicit probe executor."""

    PLANNED = "planned"
    EXECUTABLE_MISSING = "executable_missing"
    EXECUTABLE_UNSAFE = "executable_unsafe"
    EXECUTABLE_NON_EXECUTABLE = "executable_non_executable"
    PROBE_UNSUPPORTED = "probe_unsupported"
    ROUTE_NOT_FOUND = "route_not_found"


@dataclass(frozen=True)
class AgentProbePlan:
    """Exact probe intent that performs no provider process execution."""

    agent_id: str
    profile_digest: str
    route_id: str | None
    owner: str
    status: AgentProbePlanStatus
    executable_path: str | None
    arguments: tuple[str, ...]
    execution_performed: bool = False

    def __post_init__(self) -> None:
        if self.execution_performed:
            raise ValueError("agent probe plan cannot claim execution")


def plan_agent_probe(
    profile: AgentProfileV1,
    *,
    route_id: str | None = None,
    environment: Mapping[str, str] | None = None,
    facade_executable: str | os.PathLike[str] | None = None,
    platform: str,
) -> AgentProbePlan:
    """Plan a native or structured probe without spawning or persisting content."""
    if route_id is not None:
        route = next(
            (item for item in profile.structured_routes if item.route_id == route_id),
            None,
        )
        if route is None:
            return AgentProbePlan(
                profile.agent_id,
                profile.profile_digest,
                route_id,
                "structured_transport",
                AgentProbePlanStatus.ROUTE_NOT_FOUND,
                None,
                (),
            )
        command = route.command_ref
        return AgentProbePlan(
            profile.agent_id,
            profile.profile_digest,
            route_id,
            "structured_transport",
            AgentProbePlanStatus.PLANNED,
            None if command is None else command.executable_name,
            () if command is None else command.arguments,
        )
    native = profile.native
    if native is None or native.version_probe is None:
        return AgentProbePlan(
            profile.agent_id,
            profile.profile_digest,
            None,
            "native",
            AgentProbePlanStatus.PROBE_UNSUPPORTED,
            None,
            (),
        )
    native_environment = os.environ if environment is None else environment
    executable = _first_executable(
        native.executable_names,
        search_path=native_environment.get("PATH", os.defpath),
    )
    if executable is not None:
        try:
            resolved = Path(executable).resolve(strict=True)
        except OSError:
            path = None
            status = AgentProbePlanStatus.EXECUTABLE_NON_EXECUTABLE
        else:
            facade = _resolve_facade(facade_executable)
            if facade is not None and _same_file(resolved, facade):
                path = None
                status = AgentProbePlanStatus.EXECUTABLE_UNSAFE
            else:
                path = str(resolved)
                status = AgentProbePlanStatus.PLANNED
    else:
        path = None
        status = AgentProbePlanStatus.EXECUTABLE_MISSING
    if platform not in profile.platform_support:
        path = None
        status = AgentProbePlanStatus.PROBE_UNSUPPORTED
    return AgentProbePlan(
        profile.agent_id,
        profile.profile_digest,
        None,
        "native",
        status,
        path,
        native.version_probe,
    )


def _first_executable(names: tuple[str, ...], *, search_path: str) -> str | None:
    for name in names:
        executable = shutil.which(name, path=search_path)
        if executable is not None:
            return executable
    return None


def _resolve_facade(
    facade_executable: str | os.PathLike[str] | None,
) -> Path | None:
    if facade_executable is None:
        return None
    try:
        candidate = Path(facade_executable)
        if not candidate.is_absolute() and candidate.parent == Path("."):
            return None
        return candidate.resolve(strict=True)
    except OSError:
        return None


def _same_file(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right
