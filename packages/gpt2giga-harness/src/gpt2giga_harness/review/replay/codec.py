"""Review codec primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from .models import (
    MAX_TRACE_REPLAY_TARGET_CHARS,
    TraceReplayAxis,
    _HASH_RE,
    _IDENTITY_RE,
)


def _trace_replay_axis(value: Any) -> TraceReplayAxis:
    try:
        return TraceReplayAxis(str(value or "").strip())
    except ValueError as exc:
        raise ValueError(
            "trace replay axis must be model, provider, harness, or extensions"
        ) from exc


def _required_target(value: Any) -> str:
    target = _required_text(value, "target")
    if len(target) > MAX_TRACE_REPLAY_TARGET_CHARS:
        raise ValueError("trace replay target exceeds the length limit")
    return target


def _required_identity(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if not _IDENTITY_RE.fullmatch(text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _bounded_text(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if len(text) > 255 or any(char in text for char in "\r\n\x00"):
        raise ValueError(f"{field_name} is invalid")
    return text


def _required_hash(value: Any, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _HASH_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be a SHA-256 hex digest")
    return text


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _reject_unknown_fields(
    value: Mapping[str, Any], allowed: set[str] | frozenset[str]
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"unknown trace replay fields: {', '.join(unknown)}")


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
