"""Frozen cross-context ports consumed by built-in harness adapters.

The dynamic indirection keeps provider adapters on one explicit compatibility
surface until the owning runtime and session contexts publish complete facades.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_SYMBOLS = {
    "ApprovalDecision": ("gpt2giga_harness.runtime.policy", "ApprovalDecision"),
    "ApprovalStatus": ("gpt2giga_harness.runtime.models", "ApprovalStatus"),
    "EnforcementLevel": ("gpt2giga_harness.runtime.policy", "EnforcementLevel"),
    "PermissionAction": ("gpt2giga_harness.runtime.policy", "PermissionAction"),
    "PolicyContext": ("gpt2giga_harness.runtime.policy", "PolicyContext"),
    "PolicyDecision": ("gpt2giga_harness.tools.policy", "PolicyDecision"),
    "PolicyResolution": ("gpt2giga_harness.runtime.policy", "PolicyResolution"),
    "RuntimeCoordinationStore": (
        "gpt2giga_harness.runtime.store",
        "RuntimeCoordinationStore",
    ),
    "exclusive_file_lock": (
        "gpt2giga_harness.sessions.locking",
        "exclusive_file_lock",
    ),
    "utc_now": ("gpt2giga_harness.sessions.store", "utc_now"),
}


def __getattr__(name: str) -> Any:
    """Resolve one frozen owned-context symbol on first import."""
    try:
        module_name, attribute = _SYMBOLS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the frozen port surface to introspection."""
    return sorted((*globals(), *_SYMBOLS))
