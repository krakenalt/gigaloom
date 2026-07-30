"""Public facade for cross-context durable runtime access."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from gigaloom.runtime.models import (
    ApprovalStatus,
    NativeProcessOutputRecord,
    NativeProcessRecord,
    JobAttemptStatus,
    RunStatus,
    parse_run_status,
)

EnforcementLevel: Any
NativeProcessRecordNotFoundError: Any
PermissionAction: Any
PolicyContext: Any
PolicyDecision: Any
PolicyResolution: Any
RUNTIME_DB_NAME: Any
RuntimeCoordinationStore: Any

__all__ = [
    "ApprovalStatus",
    "EnforcementLevel",
    "JobAttemptStatus",
    "NativeProcessOutputRecord",
    "NativeProcessRecord",
    "NativeProcessRecordNotFoundError",
    "PermissionAction",
    "PolicyContext",
    "PolicyDecision",
    "PolicyResolution",
    "RUNTIME_DB_NAME",
    "RunStatus",
    "RuntimeCoordinationStore",
    "parse_run_status",
]

_LAZY_EXPORTS = {
    "EnforcementLevel": (
        "gigaloom.runtime.policy",
        "EnforcementLevel",
    ),
    "NativeProcessRecordNotFoundError": (
        "gigaloom.runtime.store",
        "NativeProcessRecordNotFoundError",
    ),
    "PermissionAction": (
        "gigaloom.runtime.policy",
        "PermissionAction",
    ),
    "PolicyContext": (
        "gigaloom.runtime.policy",
        "PolicyContext",
    ),
    "PolicyDecision": (
        "gigaloom.runtime.policy",
        "PolicyDecision",
    ),
    "PolicyResolution": (
        "gigaloom.runtime.policy",
        "PolicyResolution",
    ),
    "RuntimeCoordinationStore": (
        "gigaloom.runtime.store",
        "RuntimeCoordinationStore",
    ),
    "RUNTIME_DB_NAME": (
        "gigaloom.runtime.store",
        "RUNTIME_DB_NAME",
    ),
}


def __getattr__(name: str) -> Any:
    """Load policy and store implementations only when requested."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
