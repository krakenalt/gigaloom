"""Dependency-free scalar helpers for evaluation modules."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def positive_int(value: Any, default: int) -> int:
    """Return one positive integer or the supplied fallback."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def timestamp(value: str | None) -> float | None:
    """Parse one optional retained UTC timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
