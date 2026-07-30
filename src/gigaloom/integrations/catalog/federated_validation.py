# ruff: noqa: E402, F401, F403, F405
"""Read-only federated Skills and MCP catalog source contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
import re
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import anyio


FEDERATED_CATALOG_CONTRACT_VERSION = 1
SKILLS_SH_SOURCE_ID = "skills-sh"
SKILLS_SH_ORIGIN = "https://skills.sh"
NEURALDEEP_SOURCE_ID = "neuraldeep"
NEURALDEEP_ORIGIN = "https://neuraldeep.ru"
MAX_FEDERATED_ENTRIES = 10_000
MAX_FEDERATED_PAGE_SIZE = 500
MAX_FEDERATED_PAGES = 100
MAX_FEDERATED_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FEDERATED_QUERY_LENGTH = 200
MAX_FEDERATED_TEXT_LENGTH = 512
FEDERATED_TIMEOUT_SECONDS = 20.0

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_HEX_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_SKILLS_SH_ITEM_FIELDS = {
    "id",
    "slug",
    "name",
    "source",
    "installs",
    "sourceType",
    "installUrl",
    "url",
    "isDuplicate",
    "installsYesterday",
    "change",
}
_NEURALDEEP_ITEM_FIELDS = {
    "id",
    "name",
    "owner",
    "repo",
    "description",
    "installs",
    "trending24h",
    "category",
    "tags",
    "contentPath",
    "authorName",
    "telegramLink",
    "featured",
    "type",
    "status",
    "githubStars",
    "createdAt",
    "updatedAt",
    "authorId",
    "license",
    "url",
    "install",
    "source",
    "score",
    "_count",
}


def _strict_mapping(value: Any, allowed: set[str]) -> Mapping[str, Any]:
    from .federated_models import _FederatedFailure, _SchemaDrift

    if not isinstance(value, Mapping):
        raise _FederatedFailure("source.invalid_payload", "ObjectType")
    if set(value) - allowed:
        raise _SchemaDrift()
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    from .federated_models import _FederatedFailure

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _FederatedFailure("source.duplicate_field", "DuplicateField")
        result[key] = value
    return result


def _validate_query(value: Any) -> str:
    _validate_text(value, "federated query", maximum=MAX_FEDERATED_QUERY_LENGTH)
    normalized = value.strip()
    if len(normalized) < 2:
        raise ValueError("federated query is too short")
    return normalized


def _validate_limit(
    value: Any, *, field: str, maximum: int = MAX_FEDERATED_PAGE_SIZE
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > maximum
    ):
        raise ValueError(f"{field} is invalid")


def _id(value: Any, field: str) -> str:
    _validate_id(value, field)
    return value


def _validate_id(value: Any, field: str) -> None:
    from .federated_models import _FederatedFailure

    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise _FederatedFailure(
            "source.invalid_payload", f"Invalid{_error_label(field)}"
        )


def _text(value: Any, field: str) -> str:
    _validate_text(value, field)
    return value


def _validate_text(
    value: Any, field: str, *, maximum: int = MAX_FEDERATED_TEXT_LENGTH
) -> None:
    from .federated_models import _FederatedFailure

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise _FederatedFailure(
            "source.invalid_payload", f"Invalid{_error_label(field)}"
        )


def _integer(value: Any, field: str) -> int:
    from .federated_models import _FederatedFailure

    if isinstance(value, bool) or not isinstance(value, int):
        raise _FederatedFailure(
            "source.invalid_payload", f"Invalid{_error_label(field)}"
        )
    return value


def _https_url(value: Any, field: str) -> str:
    from .federated_models import _FederatedFailure

    if not isinstance(value, str):
        raise _FederatedFailure(
            "source.invalid_payload", f"Invalid{_error_label(field)}"
        )
    try:
        _validate_https_url(value)
    except ValueError as exc:
        raise _FederatedFailure(
            "source.invalid_payload", f"Invalid{_error_label(field)}"
        ) from exc
    return value


def _validate_https_url(value: str) -> None:
    parsed = urllib_parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.hostname != parsed.hostname.casefold()
        or parsed.port not in {None, 443}
    ):
        raise ValueError("URL must be canonical HTTPS")


def _validate_loopback_http_url(value: str) -> None:
    parsed = urllib_parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("URL must be loopback HTTP")


def _validate_relative_path(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 512
        or value.startswith(("/", "\\"))
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("federated relative path is invalid")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    return next(
        (value for key, value in headers.items() if key.casefold() == name.casefold()),
        None,
    )


def _origin_for_url(value: str) -> str:
    _validate_https_url(value)
    parsed = urllib_parse.urlsplit(value)
    return f"https://{parsed.hostname}"


def _canonical_https_origin(value: str) -> str:
    _validate_https_url(value)
    parsed = urllib_parse.urlsplit(value)
    if parsed.path or parsed.query:
        raise ValueError("origin cannot contain a path or query")
    return value


def _validate_sha256_ref(value: str) -> None:
    if not value.startswith("sha256:") or _HEX_HASH_RE.fullmatch(value[7:]) is None:
        raise ValueError("immutable ref must be a sha256 digest")


def _format_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("federated clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("federated timestamp must be text")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("federated timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("federated timestamp must include a timezone")
    return parsed


def _safe_error_type(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._~-]", "", value)[:64]
    return normalized or "Error"


def _error_label(value: str) -> str:
    return "".join(part.capitalize() for part in re.findall(r"[A-Za-z0-9]+", value))[
        :48
    ]


__all__ = [name for name in globals() if not name.startswith("__")]
