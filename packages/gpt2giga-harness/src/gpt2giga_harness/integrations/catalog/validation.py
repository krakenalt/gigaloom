# ruff: noqa: E402, F401, F403, F405
"""Durable integration catalog and offline MCP subregistry contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

import anyio

from gpt2giga_harness.integration_packages import (
    IntegrationPackage,
    IntegrationSourceType,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_from_dict,
    integration_package_semantic_hash,
    integration_package_to_dict,
)
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock
from gpt2giga_harness.types import REDACTED, redact_secrets


CATALOG_SCHEMA_VERSION = 1
OFFICIAL_MCP_REGISTRY_SOURCE_ID = "official-mcp-registry"
OFFICIAL_MCP_REGISTRY_BASE_URL = "https://registry.modelcontextprotocol.io"
OFFICIAL_MCP_REGISTRY_API_VERSION = "v0.1"
MAX_CATALOG_ENTRIES = 50_000
MAX_CATALOG_SOURCE_ENTRIES = 10_000
MAX_CATALOG_SOURCES = 100
MAX_CATALOG_PAGE_SIZE = 1_000
MAX_CATALOG_SOURCE_ERRORS = 20
MAX_CATALOG_JSON_BYTES = 256 * 1024
MAX_CATALOG_JSON_DEPTH = 20
MAX_REGISTRY_PAGES = 1_000
MAX_REGISTRY_RESPONSE_BYTES = 2 * 1024 * 1024
_MCP_OFFICIAL_META_KEY = "io.modelcontextprotocol.registry/official"
_LOCAL_SUBREGISTRY_META_KEY = "agent_workbench.catalog/v1"
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_MCP_NAME_RE = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9._-]+\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_IMMUTABLE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,511}\Z")


def _sanitize_json(value: Any) -> Any:
    sanitized = _sanitize_json_value(value, depth=0, secret_input=False)
    sanitized = redact_secrets(sanitized)
    try:
        encoded = json.dumps(
            sanitized,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("catalog source metadata must be JSON-compatible") from exc
    if len(encoded) > MAX_CATALOG_JSON_BYTES:
        raise ValueError("catalog source metadata is too large")
    return json.loads(encoded.decode("utf-8"))


def _sanitize_json_value(value: Any, *, depth: int, secret_input: bool) -> Any:
    if depth > MAX_CATALOG_JSON_DEPTH:
        raise ValueError("catalog source metadata is too deeply nested")
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("catalog source metadata keys must be text")
        marks_secret = value.get("isSecret") is True
        return {
            key: (
                REDACTED
                if (secret_input or marks_secret) and key in {"value", "default"}
                else _sanitize_json_value(
                    item,
                    depth=depth + 1,
                    secret_input=(secret_input or marks_secret),
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_json_value(item, depth=depth + 1, secret_input=secret_input)
            for item in value
        ]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("catalog source metadata must be JSON-compatible")


def _normalize_mcp_response(value: Any) -> dict[str, Any]:
    from .models import CatalogEntryStatus

    if not isinstance(value, Mapping):
        raise ValueError("MCP registry response must be an object")
    if set(value) - {"server", "_meta"}:
        raise ValueError("MCP registry response contains unknown fields")
    safe = _sanitize_json(value)
    server = safe.get("server")
    if not isinstance(server, Mapping):
        raise ValueError("MCP registry response requires server metadata")
    name = server.get("name")
    version = server.get("version")
    description = server.get("description")
    _validate_mcp_name(name)
    _validate_identity(version, field_name="MCP version")
    if (
        not isinstance(description, str)
        or not description.strip()
        or len(description) > 100
    ):
        raise ValueError("MCP description is invalid")
    metadata = safe.get("_meta", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("MCP registry metadata must be an object")
    official = metadata.get(_MCP_OFFICIAL_META_KEY, {})
    if not isinstance(official, Mapping):
        raise ValueError("official MCP registry metadata must be an object")
    if set(official) - {
        "status",
        "statusMessage",
        "statusChangedAt",
        "publishedAt",
        "updatedAt",
        "isLatest",
    }:
        raise ValueError("official MCP registry metadata contains unknown fields")
    status = official.get("status", CatalogEntryStatus.ACTIVE.value)
    try:
        CatalogEntryStatus(status)
    except (TypeError, ValueError) as exc:
        raise ValueError("official MCP registry status is invalid") from exc
    status_message = official.get("statusMessage")
    if status_message is not None and (
        not isinstance(status_message, str) or len(status_message) > 500
    ):
        raise ValueError("official MCP registry statusMessage is invalid")
    for field_name in ("statusChangedAt", "publishedAt", "updatedAt"):
        timestamp = official.get(field_name)
        if timestamp is not None:
            _parse_timestamp(timestamp)
    is_latest = official.get("isLatest")
    if is_latest is not None and not isinstance(is_latest, bool):
        raise ValueError("official MCP registry isLatest is invalid")
    return safe


def _mcp_status(response: Mapping[str, Any]) -> Any:
    from .models import CatalogEntryStatus

    metadata = response.get("_meta", {})
    official = metadata.get(_MCP_OFFICIAL_META_KEY, {})
    return CatalogEntryStatus(official.get("status", CatalogEntryStatus.ACTIVE.value))


def _validate_page_limit(limit: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_CATALOG_PAGE_SIZE
    ):
        raise ValueError("subregistry limit is invalid")


def _validate_include_deleted(value: Any) -> None:
    if not isinstance(value, bool):
        raise ValueError("subregistry include_deleted must be a boolean")


def _validate_import_source(
    package_source: IntegrationSourceType,
    catalog_source: Any,
) -> None:
    from .models import CatalogSourceType

    expected = {
        CatalogSourceType.LOCAL_PRIVATE: IntegrationSourceType.CURATED_CATALOG,
        CatalogSourceType.PROVIDER_MARKETPLACE: IntegrationSourceType.PROVIDER_MARKETPLACE,
        CatalogSourceType.GIT: IntegrationSourceType.GIT,
        CatalogSourceType.LOCAL: IntegrationSourceType.LOCAL,
    }.get(catalog_source)
    if expected is None or package_source is not expected:
        raise ValueError("catalog import source does not match manifest source_type")


def _catalog_id(source_id: str, package_id: str, version: str) -> str:
    _validate_identity(source_id, field_name="catalog source id")
    digest = hashlib.sha256(
        json.dumps(
            [source_id, package_id, version],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return f"catalog_{digest[:32]}"


def _entry_sort_key(entry: Any) -> tuple[str, str, str, str]:
    return (entry.package_id, entry.version, entry.source_id, entry.catalog_id)


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _read_registry_url(url: str) -> Mapping[str, Any]:
    request = urllib_request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "gpt2giga-harness-integration-catalog/1",
        },
        method="GET",
    )
    opener = urllib_request.build_opener(_NoRedirectHandler())
    with opener.open(request, timeout=20.0) as response:
        content_type = response.headers.get_content_type()
        if content_type != "application/json":
            raise ValueError("official registry returned a non-JSON response")
        body = response.read(MAX_REGISTRY_RESPONSE_BYTES + 1)
    if len(body) > MAX_REGISTRY_RESPONSE_BYTES:
        raise ValueError("official registry response is too large")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("official registry response must be an object")
    return payload


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("official registry redirects are not accepted")


def _atomic_write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
        raise ValueError(f"{field_name} contains unknown fields")
    return value


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _required_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


def _required_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _enum_value(enum_type: type[Enum], value: Any, field_name: str):
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _validate_identity(value: Any, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or _IDENTITY_RE.fullmatch(value) is None
        or redact_secrets(value) != value
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_mcp_name(value: Any) -> None:
    if (
        not isinstance(value, str)
        or len(value) < 3
        or len(value) > 200
        or _MCP_NAME_RE.fullmatch(value) is None
    ):
        raise ValueError("MCP server name is invalid")


def _validate_immutable_ref(value: Any) -> None:
    if not isinstance(value, str) or _IMMUTABLE_REF_RE.fullmatch(value) is None:
        raise ValueError("catalog immutable ref is invalid")


def _validate_bounded_metadata(value: Any, field_name: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_https_url(value: Any) -> None:
    _validate_bounded_metadata(value, "federated HTTPS URL", 2_048)
    parsed = urllib_parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or parsed.fragment
    ):
        raise ValueError("federated HTTPS URL is invalid")


def _validate_https_origin(value: Any) -> None:
    _validate_https_url(value)
    parsed = urllib_parse.urlsplit(value)
    if parsed.path or parsed.query:
        raise ValueError("federated HTTPS origin is invalid")


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


def _validate_hash(value: Any, *, field_name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _format_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime):
        raise TypeError("catalog clock must return datetime")
    if value.tzinfo is None:
        raise ValueError("catalog clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("catalog timestamp must be text")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("catalog timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError("catalog timestamp must include a timezone")
    return parsed


__all__ = [name for name in globals() if not name.startswith("__")]
