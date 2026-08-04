"""Shared bounded validation for 0.8 operational wire contracts."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from datetime import datetime
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from types import MappingProxyType
from typing import Any, Mapping, cast
from urllib.parse import urlsplit


OPERATIONAL_SCHEMA_VERSION = 1
MAX_TUPLE_ITEMS = 128
MAX_TEXT_CHARS = 4_096
MAX_TOKEN_CHARS = 1_024
MAX_JSON_NODES = 512
MAX_JSON_DEPTH = 8
MAX_JSON_BYTES = 64 * 1024

_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_INTEGRITY_RE = re.compile(
    r"(?:[0-9a-f]{64}|sha256:[0-9a-f]{64}|sha(?:256|384|512)-[A-Za-z0-9+/]+={0,2})\Z"
)
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_ENVIRONMENT_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_SENSITIVE_ENVIRONMENT_KEY_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|API_KEY|PRIVATE_KEY)"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_schema_version(value: object, *, field_name: str) -> None:
    """Require the frozen operational schema version."""
    if value != OPERATIONAL_SCHEMA_VERSION:
        raise ValueError(f"unsupported {field_name} schema_version")


def validate_identity(value: object, *, field_name: str) -> str:
    """Validate one bounded machine identity."""
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")
    return value


def validate_digest(value: object, *, field_name: str) -> str:
    """Validate one lowercase sha256 digest."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return value


def validate_optional_digest(value: object, *, field_name: str) -> str | None:
    """Validate an optional lowercase sha256 digest."""
    if value is None:
        return None
    return validate_digest(value, field_name=field_name)


def validate_integrity(value: object, *, field_name: str) -> str:
    """Validate a binary digest or exact package-manager integrity value."""
    if (
        not isinstance(value, str)
        or len(value) > 512
        or _INTEGRITY_RE.fullmatch(value) is None
    ):
        raise ValueError(f"{field_name} has an unsupported integrity format")
    return value


def validate_optional_integrity(value: object, *, field_name: str) -> str | None:
    """Validate optional binary or package-manager integrity evidence."""
    if value is None:
        return None
    return validate_integrity(value, field_name=field_name)


def validate_text(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = False,
    max_chars: int = MAX_TEXT_CHARS,
) -> str:
    """Validate bounded non-control text."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    if (not allow_empty and not value) or len(value) > max_chars:
        raise ValueError(f"{field_name} has an invalid length")
    if _CONTROL_RE.search(value):
        raise ValueError(f"{field_name} contains control characters")
    return value


def validate_token(value: object, *, field_name: str) -> str:
    """Validate one tokenized command or argument without shell parsing."""
    return validate_text(
        value,
        field_name=field_name,
        max_chars=MAX_TOKEN_CHARS,
    )


def normalize_tokens(
    values: object,
    *,
    field_name: str,
    allow_empty: bool = True,
    maximum: int = MAX_TUPLE_ITEMS,
) -> tuple[str, ...]:
    """Validate an ordered tuple of inert command tokens."""
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a bounded tuple")
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(
        validate_token(value, field_name=f"{field_name} item") for value in values
    )


def normalize_identities(
    values: object,
    *,
    field_name: str,
    allow_empty: bool = True,
    maximum: int = MAX_TUPLE_ITEMS,
    sort_values: bool = True,
) -> tuple[str, ...]:
    """Validate a unique bounded tuple of identities."""
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a bounded tuple")
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    normalized = tuple(
        validate_identity(value, field_name=f"{field_name} item") for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(normalized)) if sort_values else normalized


def normalize_digests(
    values: object,
    *,
    field_name: str,
    maximum: int = MAX_TUPLE_ITEMS,
) -> tuple[str, ...]:
    """Validate and sort a unique bounded tuple of digests."""
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError(f"{field_name} must be a bounded tuple")
    normalized = tuple(
        validate_digest(value, field_name=f"{field_name} item") for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must be unique")
    return tuple(sorted(normalized))


def normalize_environment(
    values: object,
    *,
    field_name: str = "environment",
) -> tuple[tuple[str, str], ...]:
    """Validate bounded inert environment configuration, never secrets."""
    if not isinstance(values, tuple) or len(values) > 64:
        raise ValueError(f"{field_name} must be a bounded tuple")
    normalized: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{field_name} entries must be key/value tuples")
        key, value = item
        if not isinstance(key, str) or _ENVIRONMENT_KEY_RE.fullmatch(key) is None:
            raise ValueError(f"{field_name} key is invalid")
        if _SENSITIVE_ENVIRONMENT_KEY_RE.search(key):
            raise ValueError(f"{field_name} cannot contain a secret-bearing key")
        normalized.append((key, validate_text(value, field_name=f"{field_name} value")))
    normalized.sort()
    if len({key for key, _ in normalized}) != len(normalized):
        raise ValueError(f"{field_name} keys must be unique")
    return tuple(normalized)


def validate_timestamp(value: object, *, field_name: str) -> datetime:
    """Require one timezone-aware datetime."""
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def parse_timestamp(value: object, *, field_name: str) -> datetime:
    """Decode one ISO 8601 timestamp into a timezone-aware datetime."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO timestamp") from error
    return validate_timestamp(parsed, field_name=field_name)


def timestamps_match(left: object, right: object) -> bool:
    """Compare equal revision values or valid ISO timestamps by instant."""
    if left == right:
        return True
    try:
        return parse_timestamp(left, field_name="left timestamp") == parse_timestamp(
            right,
            field_name="right timestamp",
        )
    except ValueError:
        return False


def validate_time_range(
    started_at: datetime,
    finished_at: datetime,
    *,
    field_name: str,
    allow_equal: bool = True,
) -> None:
    """Require a monotonic bounded lifecycle range."""
    validate_timestamp(started_at, field_name=f"{field_name} started_at")
    validate_timestamp(finished_at, field_name=f"{field_name} finished_at")
    if finished_at < started_at or (finished_at == started_at and not allow_equal):
        raise ValueError(f"{field_name} finish precedes start")


def validate_absolute_path(value: object, *, field_name: str) -> str:
    """Validate an absolute normalized POSIX contract path."""
    text = validate_text(value, field_name=field_name, max_chars=2_048)
    path = PurePosixPath(text)
    if not path.is_absolute() or ".." in path.parts or text != path.as_posix():
        raise ValueError(f"{field_name} must be a normalized absolute path")
    return text


def validate_relative_path(value: object, *, field_name: str) -> str:
    """Validate a non-escaping normalized relative POSIX path."""
    text = validate_text(value, field_name=field_name, max_chars=2_048)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or text != path.as_posix():
        raise ValueError(f"{field_name} must be a normalized relative path")
    return text


def contains_absolute_path(value: str) -> bool:
    """Return whether a token embeds a POSIX, Windows, or file-URL path."""
    candidates = (value, value.split("=", 1)[-1])
    return any(
        candidate.startswith("file://")
        or PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
        for candidate in candidates
    )


def validate_https_url(value: object, *, field_name: str) -> str:
    """Validate one credential-free HTTPS URL."""
    text = validate_text(value, field_name=field_name, max_chars=2_048)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be a credential-free HTTPS URL")
    return text


def validate_network_origin(value: object, *, field_name: str) -> str:
    """Validate one exact HTTPS origin with no path/query/credentials."""
    text = validate_https_url(value, field_name=field_name)
    parsed = urlsplit(text)
    if parsed.path not in {"", "/"} or parsed.query:
        raise ValueError(f"{field_name} must be an origin")
    return text.rstrip("/")


def validate_local_origin(value: object, *, field_name: str) -> str:
    """Validate one loopback HTTP(S) browser origin."""
    text = validate_text(value, field_name=field_name, max_chars=2_048)
    parsed = urlsplit(text)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be an exact loopback origin")
    return text.rstrip("/")


def require_mapping(
    value: object,
    *,
    required: set[str],
    optional: AbstractSet[str] = frozenset(),
    field_name: str,
) -> Mapping[str, Any]:
    """Require an object with no missing or unknown fields."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} keys must be strings")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ValueError(f"{field_name} is missing fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"{field_name} has unknown fields: {sorted(unknown)}")
    return cast(Mapping[str, Any], value)


def freeze_json_object(value: object, *, field_name: str) -> Mapping[str, object]:
    """Validate and deeply freeze one bounded JSON object."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON object")
    counter = [0]
    frozen = _freeze_json(value, depth=0, counter=counter, field_name=field_name)
    if not isinstance(frozen, Mapping):  # pragma: no cover - root is checked above
        raise ValueError(f"{field_name} must be a JSON object")
    if len(canonical_json_bytes(frozen)) > MAX_JSON_BYTES:
        raise ValueError(f"{field_name} exceeds the JSON byte limit")
    return cast(Mapping[str, object], frozen)


def thaw_json(value: object) -> object:
    """Convert a frozen JSON value into ordinary wire containers."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one validated JSON value deterministically."""
    return json.dumps(
        thaw_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    """Digest one validated JSON-compatible value deterministically."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _freeze_json(
    value: object,
    *,
    depth: int,
    counter: list[int],
    field_name: str,
) -> object:
    counter[0] += 1
    if counter[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise ValueError(f"{field_name} exceeds JSON bounds")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str):
            if len(value) > MAX_TEXT_CHARS or "\0" in value or "\x1b" in value:
                raise ValueError(f"{field_name} contains invalid string data")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{field_name} contains a non-string object key")
        typed = cast(Mapping[str, object], value)
        normalized: dict[str, object] = {}
        for key in sorted(typed):
            validate_identity(key, field_name=f"{field_name} key")
            normalized[key] = _freeze_json(
                typed[key],
                depth=depth + 1,
                counter=counter,
                field_name=field_name,
            )
        return MappingProxyType(normalized)
    if isinstance(value, (tuple, list)):
        if len(value) > MAX_TUPLE_ITEMS:
            raise ValueError(f"{field_name} contains an oversized array")
        return tuple(
            _freeze_json(
                item,
                depth=depth + 1,
                counter=counter,
                field_name=field_name,
            )
            for item in value
        )
    raise ValueError(f"{field_name} contains a non-JSON value")
