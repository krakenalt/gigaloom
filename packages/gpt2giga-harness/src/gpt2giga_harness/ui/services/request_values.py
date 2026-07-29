"""Strict scalar normalization for UI request payloads."""

from typing import Any


def optional_text(value: Any) -> str | None:
    """Normalize an optional value to non-empty text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def required_text(value: Any, message: str) -> str:
    """Normalize required text or raise the caller-provided validation error."""
    text = optional_text(value)
    if text is None:
        raise ValueError(message)
    return text


def optional_float(value: Any) -> float | None:
    """Parse an optional floating-point value."""
    if value is None:
        return None
    return float(value)


def optional_int(value: Any) -> int | None:
    """Parse an optional integer value."""
    if value is None or str(value).strip() == "":
        return None
    return int(value)
