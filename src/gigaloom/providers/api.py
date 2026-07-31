"""Lazy public facade for provider configuration diagnostics."""

from __future__ import annotations

from importlib import import_module
from typing import Any

ProviderSettingsService: Any

__all__ = ["ProviderSettingsService"]


def __getattr__(name: str) -> Any:
    """Resolve one provider service without eager protocol imports."""
    if name != "ProviderSettingsService":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(
        import_module("gigaloom.providers.settings"),
        "ProviderSettingsService",
    )
    globals()[name] = value
    return value
