# ruff: noqa: E402, F401, F403, F405
"""Provider-neutral integration package and target discovery contracts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
from importlib.metadata import entry_points
import json
import re
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import urlsplit, urlunsplit

from gigaloom.registries import (
    EntryPointFamily,
    RegistrationOutcome,
    RegistryCollisionError,
    VersionedRegistryKernel,
)
from gigaloom.types import redact_secrets

if TYPE_CHECKING:
    from .models import InstallationScope


INTEGRATION_PACKAGE_SCHEMA_VERSION = 1
EXTENSION_TARGET_SCHEMA_VERSION = 1
NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP = "gigaloom.extension_targets.v1"
EXTENSION_TARGET_ENTRY_POINTS = EntryPointFamily(
    registry_id="extension_target",
    api_version=1,
    primary_group=NEUTRAL_EXTENSION_TARGET_ENTRY_POINT_GROUP,
)
MAX_TARGET_DISCOVERY_ERRORS = 20
MAX_TARGET_DISCOVERY_ERROR_CHARS = 400
MAX_TRUST_DIAGNOSTICS = 100
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_CHECKSUM_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _strict_mapping(
    value: Any,
    *,
    allowed: set[str],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{field_name} contains unknown fields: {sorted(unknown)!r}")
    return value


def _required_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return value


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    _validate_text(value, field_name=field_name)
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name)


def _enum_value(enum_type: type[Enum], value: Any, *, field_name: str):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _validate_identity(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_text(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} contains control characters")
    if len(value) > 2048:
        raise ValueError(f"{field_name} is too long")
    if redact_secrets(value) != value:
        raise ValueError(f"{field_name} contains secret material")


def _validate_checksum(value: str, *, field_name: str) -> None:
    if not isinstance(value, str) or _CHECKSUM_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")


def _normalize_argv(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(values)
    for value in normalized:
        _validate_text(value, field_name="requirement argv")
    return normalized


def _normalize_environment_names(values: Iterable[str]) -> tuple[str, ...]:
    raw_values = tuple(values)
    if any(not isinstance(value, str) for value in raw_values):
        raise ValueError("requirement environment name is invalid")
    normalized = tuple(sorted(set(raw_values)))
    if any(_ENV_NAME_RE.fullmatch(value) is None for value in normalized):
        raise ValueError("requirement environment name is invalid")
    return normalized


def _normalize_identities(
    values: Iterable[str],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} is required")
    for value in normalized:
        _validate_identity(value, field_name=field_name)
    return normalized


def _normalize_records(
    values: Iterable[Any],
    *,
    expected_type: type[Any],
    field_name: str,
    id_attribute: str = "id",
    allow_empty: bool = False,
) -> tuple[Any, ...]:
    normalized = tuple(values)
    if not normalized and not allow_empty:
        raise ValueError(f"{field_name} is required")
    if any(not isinstance(item, expected_type) for item in normalized):
        raise ValueError(f"{field_name} is invalid")
    normalized = tuple(sorted(normalized, key=lambda item: getattr(item, id_attribute)))
    ids = [getattr(item, id_attribute) for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{field_name} contains duplicate ids")
    return normalized


def _normalize_scopes(
    values: Iterable["InstallationScope"],
) -> tuple["InstallationScope", ...]:
    from .models import InstallationScope

    raw_values = tuple(values)
    if not raw_values or any(
        not isinstance(item, InstallationScope) for item in raw_values
    ):
        raise ValueError("installation scopes are invalid")
    normalized = tuple(sorted(set(raw_values), key=lambda item: item.value))
    return normalized


def _canonical_https_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("network requirement must be an HTTPS origin")
    return urlunsplit(("https", parsed.netloc.lower(), "", "", ""))


__all__ = [name for name in globals() if not name.startswith("__")]
