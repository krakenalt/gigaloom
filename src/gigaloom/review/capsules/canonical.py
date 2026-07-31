"""Canonical JSON and strict content-free validation primitives."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import CapsuleSchemaError

SHA256_RE = re.compile(r"[0-9a-f]{64}")
IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,254}")
GIT_REVISION_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
OMISSION_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")

_FORBIDDEN_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "raw_prompt",
        "source_content",
        "source_files",
        "patch",
        "patch_bytes",
        "terminal_transcript",
        "transcript",
        "tool_output",
        "tool_outputs",
        "secret",
        "secrets",
        "environment_values",
        "env_values",
        "provider_hidden_state",
        "private_repository_url",
        "repository_url",
    }
)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON value deterministically after rejecting ambiguous types."""
    _validate_json_value(value, "document")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 digest of canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def detached_json(value: Any) -> Any:
    """Return a detached JSON-compatible copy with canonical key ordering."""
    return json.loads(canonical_json_bytes(value))


def parse_canonical_json_bytes(data: bytes, field: str) -> dict[str, Any]:
    """Parse exact canonical JSON while rejecting duplicate object keys."""

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise CapsuleSchemaError(f"{field} contains a duplicate key")
            value[key] = item
        return value

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs_hook)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapsuleSchemaError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise CapsuleSchemaError(f"{field} must be a JSON object")
    if canonical_json_bytes(value) != data:
        raise CapsuleSchemaError(f"{field} is not canonical JSON")
    return value


def require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CapsuleSchemaError(f"{field} must be an object")
    return value


def require_exact_fields(
    value: Mapping[str, Any], field: str, expected: set[str] | frozenset[str]
) -> None:
    if set(value) != set(expected):
        raise CapsuleSchemaError(f"{field} fields are invalid")


def require_string(value: Any, field: str, *, max_length: int = 255) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise CapsuleSchemaError(f"{field} must be a bounded non-empty string")
    if any(character in value for character in "\r\n\x00"):
        raise CapsuleSchemaError(f"{field} contains unsafe characters")
    return value


def require_identity(value: Any, field: str) -> str:
    text = require_string(value, field)
    if IDENTITY_RE.fullmatch(text) is None:
        raise CapsuleSchemaError(f"{field} is invalid")
    return text


def require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise CapsuleSchemaError(f"{field} must be a lowercase SHA-256 digest")
    return value


def require_optional_sha256(value: Any, field: str) -> str | None:
    return None if value is None else require_sha256(value, field)


def require_non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CapsuleSchemaError(f"{field} must be a non-negative integer")
    return value


def require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise CapsuleSchemaError(f"{field} must be a boolean")
    return value


def require_list(value: Any, field: str, *, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise CapsuleSchemaError(f"{field} must be a bounded list")
    return value


def require_hash_list(value: Any, field: str, *, limit: int = 256) -> list[str]:
    items = [
        require_sha256(item, f"{field}[{index}]")
        for index, item in enumerate(require_list(value, field, limit=limit))
    ]
    if items != sorted(set(items)):
        raise CapsuleSchemaError(f"{field} must be sorted and unique")
    return items


def require_omission_list(value: Any, field: str) -> list[str]:
    items = require_list(value, field, limit=128)
    if any(
        not isinstance(item, str) or OMISSION_RE.fullmatch(item) is None
        for item in items
    ):
        raise CapsuleSchemaError(f"{field} contains an invalid omission code")
    if items != sorted(set(items)):
        raise CapsuleSchemaError(f"{field} must be sorted and unique")
    return items


def validate_content_free(value: Any, field: str = "document") -> None:
    """Reject known raw-content and secret-bearing fields at every depth."""
    _validate_json_value(value, field)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key.lower().replace("-", "_") in _FORBIDDEN_CONTENT_KEYS:
                raise CapsuleSchemaError(
                    f"{field}.{key} is forbidden in a content-free capsule"
                )
            validate_content_free(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            validate_content_free(item, f"{field}[{index}]")


def _validate_json_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        raise CapsuleSchemaError(f"{field} must not contain floating-point values")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CapsuleSchemaError(f"{field} keys must be strings")
            _validate_json_value(item, f"{field}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{field}[{index}]")
        return
    raise CapsuleSchemaError(f"{field} contains a non-JSON value")
