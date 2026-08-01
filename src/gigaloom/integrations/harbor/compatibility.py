"""Fail-closed Harbor API version and capability checks."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import inspect
import re
from typing import Mapping


HARBOR_DISTRIBUTION_NAME = "harbor"
HARBOR_SUPPORTED_API_SERIES = ((0, 20),)
_VERSION_RE = re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\Z")


class HarborCompatibilityError(ImportError):
    """The installed optional Harbor API is absent or incompatible."""


@dataclass(frozen=True, slots=True)
class HarborCompatibilityV1:
    """Import-time evidence for the exact Harbor surface used by the adapter."""

    installed_version: str
    api_series: str
    capabilities: tuple[str, ...]
    schema_version: int = 1


def require_harbor_compatibility(
    *,
    base_agent: type[object],
    base_environment: type[object],
    agent_context: type[object],
    installed_version: str | None = None,
) -> HarborCompatibilityV1:
    """Check the supported version and exact call capabilities at import time."""
    version = installed_version or _installed_harbor_version()
    series = _version_series(version)
    if series not in HARBOR_SUPPORTED_API_SERIES:
        raise HarborCompatibilityError(
            "GigaLoom's Harbor adapter supports Harbor 0.20.x"
        )

    checks = (
        _require_parameters(
            base_agent,
            "__init__",
            {"logs_dir", "model_name", "extra_env"},
        ),
        _require_parameters(
            base_agent,
            "setup",
            {"environment"},
            require_async=True,
        ),
        _require_parameters(
            base_agent,
            "run",
            {"instruction", "environment", "context"},
            require_async=True,
        ),
        _require_parameters(
            base_environment,
            "exec",
            {"command", "cwd", "env", "timeout_sec", "user"},
            require_async=True,
        ),
        _require_parameters(
            base_environment,
            "upload_file",
            {"source_path", "target_path"},
            require_async=True,
        ),
        _require_context_metadata(agent_context),
    )
    return HarborCompatibilityV1(
        installed_version=version,
        api_series=f"{series[0]}.{series[1]}",
        capabilities=tuple(sorted(checks)),
    )


def _installed_harbor_version() -> str:
    try:
        return metadata.version(HARBOR_DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError as error:
        raise HarborCompatibilityError(
            "Harbor 0.20.x is required for the optional GigaLoom adapter"
        ) from error


def _version_series(value: str) -> tuple[int, int]:
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise HarborCompatibilityError("the Harbor version is not a stable release")
    return int(match.group("major")), int(match.group("minor"))


def _require_parameters(
    owner: type[object],
    method_name: str,
    expected: set[str],
    require_async: bool = False,
) -> str:
    method = getattr(owner, method_name, None)
    if not callable(method):
        raise HarborCompatibilityError(
            f"Harbor capability {owner.__name__}.{method_name} is unavailable"
        )
    try:
        parameters = set(inspect.signature(method).parameters)
    except (TypeError, ValueError) as error:
        raise HarborCompatibilityError(
            f"Harbor capability {owner.__name__}.{method_name} is not inspectable"
        ) from error
    if not expected <= parameters:
        raise HarborCompatibilityError(
            f"Harbor capability {owner.__name__}.{method_name} is incompatible"
        )
    if require_async and not inspect.iscoroutinefunction(method):
        raise HarborCompatibilityError(
            f"Harbor capability {owner.__name__}.{method_name} is incompatible"
        )
    return f"{owner.__name__}.{method_name}"


def _require_context_metadata(agent_context: type[object]) -> str:
    fields = getattr(agent_context, "model_fields", None)
    annotations = getattr(agent_context, "__annotations__", {})
    if not (
        (isinstance(fields, Mapping) and "metadata" in fields)
        or (isinstance(annotations, Mapping) and "metadata" in annotations)
    ):
        raise HarborCompatibilityError(
            "Harbor capability AgentContext.metadata is unavailable"
        )
    return "AgentContext.metadata"


__all__ = [
    "HARBOR_DISTRIBUTION_NAME",
    "HARBOR_SUPPORTED_API_SERIES",
    "HarborCompatibilityError",
    "HarborCompatibilityV1",
    "require_harbor_compatibility",
]
