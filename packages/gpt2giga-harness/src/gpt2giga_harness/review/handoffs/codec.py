"""Review codec primitives."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from .models import HandoffCapsuleError, _HASH_RE, _IDENTITY_RE


def _semantic_hash(domain: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


def _json_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode(
            "utf-8"
        )
    )


def _mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HandoffCapsuleError(f"handoff capsule {field_name} must be an object")
    return value


def _exact_mapping(
    value: Any,
    field_name: str,
    expected: set[str],
) -> Mapping[str, Any]:
    mapping = _mapping(value, field_name)
    _exact_fields(mapping, field_name, expected)
    return mapping


def _exact_fields(
    value: Mapping[str, Any],
    field_name: str,
    expected: set[str],
) -> None:
    if set(value) != expected:
        raise HandoffCapsuleError(f"handoff capsule {field_name} fields are invalid")


def _object_list(value: Any, field_name: str, *, limit: int) -> list[Mapping[str, Any]]:
    if (
        not isinstance(value, list)
        or len(value) > limit
        or any(not isinstance(item, Mapping) for item in value)
    ):
        raise HandoffCapsuleError(f"handoff capsule {field_name} is invalid")
    return value


def _hash_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise HandoffCapsuleError(f"handoff capsule {field_name} must be a list")
    hashes = [_required_hash(item, field_name) for item in value]
    if hashes != sorted(set(hashes)):
        raise HandoffCapsuleError(f"handoff capsule {field_name} is not canonical")
    return hashes


def _non_negative_integer(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise HandoffCapsuleError(
            f"handoff capsule {field_name} must be a non-negative integer"
        )
    return value


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_sequence(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _required_identity(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if _IDENTITY_RE.fullmatch(text) is None:
        raise HandoffCapsuleError(f"{field_name} is invalid")
    return text


def _optional_identity(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if _IDENTITY_RE.fullmatch(text) is not None else None


def _required_hash(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if _HASH_RE.fullmatch(text) is None:
        raise HandoffCapsuleError(f"{field_name} is invalid")
    return text


def _first_hash(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if _HASH_RE.fullmatch(text) is not None:
            return text
    return None
