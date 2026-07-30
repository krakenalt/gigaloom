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
from .codec import *  # noqa: F403
from .models import *  # noqa: F403
from .store import *  # noqa: F403
from .validation import *  # noqa: F403


async def sync_official_mcp_registry(
    store: IntegrationCatalogStore,
    *,
    fetch_page: RegistryPageFetcher | None = None,
    page_size: int = 100,
) -> CatalogSyncResult:
    """Fetch every official Registry page before atomically merging the cache."""
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or page_size < 1
        or page_size > MAX_CATALOG_PAGE_SIZE
    ):
        raise ValueError("registry page_size is invalid")
    fetcher = fetch_page or fetch_official_mcp_registry_page
    observed_at = _format_timestamp(store._now())
    cursor: str | None = None
    seen_cursors: set[str] = set()
    entries: dict[str, CatalogEntry] = {}
    try:
        for _page_number in range(MAX_REGISTRY_PAGES):
            payload = await fetcher(
                cursor=cursor,
                limit=page_size,
                include_deleted=True,
            )
            responses, next_cursor = _parse_registry_page(payload)
            for response in responses:
                entry = _official_registry_entry(response, observed_at=observed_at)
                existing = entries.get(entry.catalog_id)
                if existing is not None and existing.content_hash != entry.content_hash:
                    raise CatalogConflictError(
                        "official registry page contains conflicting immutable entries"
                    )
                entries[entry.catalog_id] = entry
            if len(entries) > MAX_CATALOG_SOURCE_ENTRIES:
                raise ValueError("official registry returned too many entries")
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise ValueError("official registry repeated a pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise ValueError("official registry exceeded the page bound")
    except Exception as exc:
        error = _source_error(
            code="source.fetch_failed",
            source_id=OFFICIAL_MCP_REGISTRY_SOURCE_ID,
            error_type=type(exc).__name__,
            occurred_at=observed_at,
        )
        return store._record_source_failure(
            source_id=OFFICIAL_MCP_REGISTRY_SOURCE_ID,
            source_type=CatalogSourceType.OFFICIAL_MCP_REGISTRY,
            error=error,
            observed_at=observed_at,
        )
    return store._merge_source(
        source_id=OFFICIAL_MCP_REGISTRY_SOURCE_ID,
        source_type=CatalogSourceType.OFFICIAL_MCP_REGISTRY,
        incoming=tuple(entries.values()),
        observed_at=observed_at,
        complete=True,
    )


async def fetch_official_mcp_registry_page(
    *,
    cursor: str | None,
    limit: int,
    include_deleted: bool,
) -> Mapping[str, Any]:
    """Read one bounded page from the fixed official Registry endpoint."""
    parameters: dict[str, str] = {
        "limit": str(limit),
        "include_deleted": "true" if include_deleted else "false",
    }
    if cursor is not None:
        parameters["cursor"] = cursor
    url = (
        f"{OFFICIAL_MCP_REGISTRY_BASE_URL}/{OFFICIAL_MCP_REGISTRY_API_VERSION}/"
        f"servers?{urllib_parse.urlencode(parameters)}"
    )
    return await anyio.to_thread.run_sync(lambda: _read_registry_url(url))


class MCPSubregistry:
    """Serve MCP Registry-compatible reads exclusively from the local cache."""

    def __init__(self, store: IntegrationCatalogStore) -> None:
        self.store = store

    def list_servers(
        self,
        *,
        cursor: str | None = None,
        limit: int = 100,
        search: str | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Return a deterministic local Registry page without upstream access."""
        _validate_page_limit(limit)
        _validate_include_deleted(include_deleted)
        entries = self._mcp_entries(include_deleted=include_deleted)
        if search is not None:
            if not isinstance(search, str):
                raise ValueError("subregistry search must be text")
            query = search.strip().casefold()
            if len(query) > 200:
                raise ValueError("subregistry search is too long")
            if query:
                entries = tuple(item for item in entries if _entry_matches(item, query))
        start = _cursor_start(entries, cursor)
        selected = entries[start : start + limit]
        next_cursor = None
        if start + len(selected) < len(entries) and selected:
            next_cursor = _encode_cursor(selected[-1].catalog_id)
        metadata: dict[str, Any] = {"count": len(selected)}
        if next_cursor is not None:
            metadata["nextCursor"] = next_cursor
        return {
            "servers": [_subregistry_response(item) for item in selected],
            "metadata": metadata,
        }

    def list_versions(
        self,
        server_name: str,
        *,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Return all locally cached versions for one MCP server."""
        _validate_mcp_name(server_name)
        _validate_include_deleted(include_deleted)
        entries = tuple(
            item
            for item in self._mcp_entries(include_deleted=include_deleted)
            if item.package_id == server_name
        )
        if not entries:
            raise KeyError(server_name)
        entries = tuple(sorted(entries, key=_published_at, reverse=True))
        return {
            "servers": [_subregistry_response(item) for item in entries],
            "metadata": {"count": len(entries)},
        }

    def get_version(
        self,
        server_name: str,
        version: str,
        *,
        include_deleted: bool = False,
    ) -> dict[str, Any]:
        """Return one exact or locally declared latest MCP version."""
        _validate_mcp_name(server_name)
        _validate_identity(version, field_name="MCP version")
        _validate_include_deleted(include_deleted)
        entries = tuple(
            item
            for item in self._mcp_entries(include_deleted=include_deleted)
            if item.package_id == server_name
        )
        if version == "latest":
            latest = next((item for item in entries if _is_latest(item)), None)
            if latest is None:
                raise KeyError((server_name, version))
            return _subregistry_response(latest)
        match = next((item for item in entries if item.version == version), None)
        if match is None:
            raise KeyError((server_name, version))
        return _subregistry_response(match)

    def _mcp_entries(self, *, include_deleted: bool) -> tuple[CatalogEntry, ...]:
        return tuple(
            item
            for item in self.store.list()
            if item.mcp_response is not None
            and (include_deleted or item.status is not CatalogEntryStatus.DELETED)
        )


def _official_registry_entry(
    response: Mapping[str, Any],
    *,
    observed_at: str,
) -> CatalogEntry:
    normalized = _normalize_mcp_response(response)
    server = normalized["server"]
    package_id = server["name"]
    version = server["version"]
    return CatalogEntry(
        catalog_id=_catalog_id(OFFICIAL_MCP_REGISTRY_SOURCE_ID, package_id, version),
        source_id=OFFICIAL_MCP_REGISTRY_SOURCE_ID,
        source_type=CatalogSourceType.OFFICIAL_MCP_REGISTRY,
        package_id=package_id,
        version=version,
        immutable_ref=f"{package_id}@{version}",
        content_hash=_json_hash(server),
        status=_mcp_status(normalized),
        pinned=True,
        source_present=True,
        install_authorized=False,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        mcp_response=normalized,
    )


def _parse_registry_page(
    payload: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], str | None]:
    if not isinstance(payload, Mapping):
        raise ValueError("official registry page must be an object")
    if set(payload) - {"servers", "metadata"}:
        raise ValueError("official registry page contains unknown fields")
    servers = payload.get("servers")
    if not isinstance(servers, list) or len(servers) > MAX_CATALOG_PAGE_SIZE:
        raise ValueError("official registry servers are invalid")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("official registry metadata must be an object")
    count = metadata.get("count")
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count != len(servers)
    ):
        raise ValueError("official registry page count does not match")
    cursor = metadata.get("nextCursor")
    if cursor is not None:
        if not isinstance(cursor, str) or len(cursor) > 2_048:
            raise ValueError("official registry nextCursor is invalid")
        cursor = cursor or None
    return tuple(_normalize_mcp_response(item) for item in servers), cursor


def _normalize_mcp_response(value: Any) -> dict[str, Any]:
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


def _subregistry_response(entry: CatalogEntry) -> dict[str, Any]:
    response = _sanitize_json(entry.mcp_response)
    metadata = dict(response.get("_meta", {}))
    metadata[_LOCAL_SUBREGISTRY_META_KEY] = {
        "catalogId": entry.catalog_id,
        "sourceId": entry.source_id,
        "sourceType": entry.source_type.value,
        "immutableRef": entry.immutable_ref,
        "contentHash": entry.content_hash,
        "pinned": True,
        "sourcePresent": entry.source_present,
        "installAuthorized": False,
        "trustDecision": entry.trust_decision.value,
    }
    response["_meta"] = metadata
    return response


def _mcp_status(response: Mapping[str, Any]) -> CatalogEntryStatus:
    metadata = response.get("_meta", {})
    official = metadata.get(_MCP_OFFICIAL_META_KEY, {})
    return CatalogEntryStatus(official.get("status", CatalogEntryStatus.ACTIVE.value))


def _is_latest(entry: CatalogEntry) -> bool:
    if entry.mcp_response is None:
        return False
    metadata = entry.mcp_response.get("_meta", {})
    official = metadata.get(_MCP_OFFICIAL_META_KEY, {})
    return official.get("isLatest") is True


def _published_at(entry: CatalogEntry) -> datetime:
    if entry.mcp_response is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    metadata = entry.mcp_response.get("_meta", {})
    official = metadata.get(_MCP_OFFICIAL_META_KEY, {})
    value = official.get("publishedAt")
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _parse_timestamp(value).astimezone(timezone.utc)


def _entry_matches(entry: CatalogEntry, query: str) -> bool:
    if entry.mcp_response is None:
        return False
    server = entry.mcp_response["server"]
    values = (server.get("name"), server.get("title"), server.get("description"))
    return any(query in str(value).casefold() for value in values if value is not None)


def _cursor_start(entries: Sequence[CatalogEntry], cursor: str | None) -> int:
    if cursor is None:
        return 0
    catalog_id = _decode_cursor(cursor)
    for index, entry in enumerate(entries):
        if entry.catalog_id == catalog_id:
            return index + 1
    raise ValueError("subregistry cursor does not match the current query")


def _encode_cursor(catalog_id: str) -> str:
    return (
        base64.urlsafe_b64encode(catalog_id.encode("utf-8")).decode("ascii").rstrip("=")
    )


def _decode_cursor(cursor: str) -> str:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise ValueError("subregistry cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("subregistry cursor is invalid") from exc
    _validate_identity(decoded, field_name="catalog id")
    return decoded
