"""Unified Harness browser UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gigaloom.ui.app import create_app, validate_ui_bind

__all__ = ["create_app", "validate_ui_bind"]


def __getattr__(name: str) -> Any:
    """Resolve public FastAPI helpers only when callers request them."""
    if name in __all__:
        from gigaloom.ui import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
