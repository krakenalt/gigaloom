"""Lazy public facade for content-free performance diagnostics."""

from __future__ import annotations

from importlib import import_module
from typing import Any

run_performance_baseline: Any
write_performance_report: Any

__all__ = ["run_performance_baseline", "write_performance_report"]

_LAZY_EXPORTS = {
    "run_performance_baseline": (
        "gigaloom.diagnostics.performance.baseline",
        "run_performance_baseline",
    ),
    "write_performance_report": (
        "gigaloom.diagnostics.performance.reports",
        "write_performance_report",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve one performance operation without eager workload imports."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
