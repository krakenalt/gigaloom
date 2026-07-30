"""Unified Harness package for local gpt2giga smoke and agent runs."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("gigaloom")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0+unknown"

__all__ = [
    "__version__",
    "Availability",
    "AvailabilityStatus",
    "GigaChatApiMode",
    "HarnessCapability",
    "HarnessChatMessage",
    "HarnessRegistry",
    "HarnessRequest",
    "HarnessResult",
    "HarnessSpec",
    "create_default_registry",
    "emit_event",
]

_LAZY_EXPORTS = {
    "HarnessRegistry": ("gigaloom.registry", "HarnessRegistry"),
    "create_default_registry": (
        "gigaloom.registry",
        "create_default_registry",
    ),
    "Availability": ("gigaloom.contracts.harness", "Availability"),
    "AvailabilityStatus": (
        "gigaloom.contracts.harness",
        "AvailabilityStatus",
    ),
    "GigaChatApiMode": (
        "gigaloom.contracts.providers",
        "GigaChatApiMode",
    ),
    "HarnessCapability": (
        "gigaloom.contracts.harness",
        "HarnessCapability",
    ),
    "HarnessChatMessage": (
        "gigaloom.contracts.harness",
        "HarnessChatMessage",
    ),
    "HarnessRequest": ("gigaloom.contracts.harness", "HarnessRequest"),
    "HarnessResult": ("gigaloom.contracts.harness", "HarnessResult"),
    "HarnessSpec": ("gigaloom.contracts.harness", "HarnessSpec"),
    "emit_event": ("gigaloom.contracts.events", "emit_event"),
}


def __getattr__(name: str) -> Any:
    """Load public runtime contracts only when callers request them."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
