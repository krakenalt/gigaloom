"""Typed values projections for TUI clients."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


from gigaloom.tui.contracts import MAX_DISPLAY_CHARS, WorkbenchClientError


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_BIDI_CONTROL_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
_TERMINAL_SEQUENCE_RE = re.compile(
    r"\x1b(?:"
    r"\][^\x07\x1b]*(?:\x07|\x1b\\)?|"
    r"P.*?(?:\x1b\\|$)|"
    r"\[[0-?]*[ -/]*[@-~]|"
    r"[@-_]"
    r")",
    re.DOTALL,
)


def _safe_paths(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_display_text(item) for item in value[:100])


def _bounded_non_negative_int(value: Any) -> int:
    parsed = _optional_non_negative_int(value)
    return parsed if parsed is not None else 0


def _optional_non_negative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _bounded_content_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "")
    return _neutralize_presentation_text(text)[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mapping_items(value: Any, limit: int) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value[:limit] if isinstance(item, Mapping))


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise WorkbenchClientError(f"{field_name} is missing")
    return text


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:MAX_DISPLAY_CHARS]


def _display_text(value: Any) -> str:
    text = _neutralize_presentation_text(str(value or ""))
    return text.replace("\r", " ").replace("\n", " ")[:MAX_DISPLAY_CHARS]


def _optional_display_text(value: Any) -> str | None:
    text = _optional_text(value)
    return _display_text(text) if text is not None else None


def _required_identity(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@+~-]{0,255}", text):
        raise WorkbenchClientError(f"{field_name} is invalid")
    return text


def _path_identity(value: Any) -> str:
    return _required_identity(value, "path identity")


def _required_content(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkbenchClientError(f"{field_name} is required")
    if len(value) > 32_768:
        raise WorkbenchClientError(f"{field_name} exceeds the size limit")
    return value


def _hash_generation(value: str | None) -> int:
    if not value:
        return 1
    return max(int(hashlib.sha256(value.encode()).hexdigest()[:8], 16), 1)


def _bounded_event_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "")
    return _neutralize_presentation_text(text)[:8192]


def _neutralize_presentation_text(value: str) -> str:
    """Neutralize control sequences and bidi overrides for all TUI surfaces."""
    text = _TERMINAL_SEQUENCE_RE.sub("⟦terminal-control⟧", value)
    text = _BIDI_CONTROL_RE.sub("�", text)
    return _CONTROL_RE.sub("�", text)


def _optional_identity(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return _required_identity(value, "event identity")
    except WorkbenchClientError:
        return None
