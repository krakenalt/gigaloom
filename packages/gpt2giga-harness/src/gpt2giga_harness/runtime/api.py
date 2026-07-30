"""Public facade for cross-context durable runtime access."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from gpt2giga_harness.runtime.models import (
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
        "gpt2giga_harness.runtime.policy",
        "EnforcementLevel",
    ),
    "NativeProcessRecordNotFoundError": (
        "gpt2giga_harness.runtime.store",
        "NativeProcessRecordNotFoundError",
    ),
    "PermissionAction": (
        "gpt2giga_harness.runtime.policy",
        "PermissionAction",
    ),
    "PolicyContext": (
        "gpt2giga_harness.runtime.policy",
        "PolicyContext",
    ),
    "PolicyDecision": (
        "gpt2giga_harness.runtime.policy",
        "PolicyDecision",
    ),
    "PolicyResolution": (
        "gpt2giga_harness.runtime.policy",
        "PolicyResolution",
    ),
    "RuntimeCoordinationStore": (
        "gpt2giga_harness.runtime.store",
        "RuntimeCoordinationStore",
    ),
    "RUNTIME_DB_NAME": (
        "gpt2giga_harness.runtime.store",
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
