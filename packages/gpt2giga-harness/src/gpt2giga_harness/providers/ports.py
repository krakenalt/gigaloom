"""Frozen runtime ports consumed by provider execution adapters."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "NetworkAccessTicket": (
        "gpt2giga_harness.runtime.network_access",
        "NetworkAccessTicket",
    ),
    "ScopedNetworkRequest": (
        "gpt2giga_harness.runtime.network_access",
        "ScopedNetworkRequest",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve one frozen runtime contract without importing runtime storage."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the frozen provider port surface to introspection."""
    return sorted({*globals(), *_EXPORTS})
