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
from .models import *  # noqa: F403
from .validation import *  # noqa: F403


def catalog_entry_to_dict(entry: CatalogEntry) -> dict[str, Any]:
    """Serialize one local catalog entry without implying install authority."""
    return {
        "catalog_id": entry.catalog_id,
        "source_id": entry.source_id,
        "source_type": entry.source_type.value,
        "package_id": entry.package_id,
        "version": entry.version,
        "immutable_ref": entry.immutable_ref,
        "content_hash": entry.content_hash,
        "status": entry.status.value,
        "pinned": entry.pinned,
        "source_present": entry.source_present,
        "install_authorized": entry.install_authorized,
        "trust_decision": entry.trust_decision.value,
        "first_seen_at": entry.first_seen_at,
        "last_seen_at": entry.last_seen_at,
        "federated": (
            _federated_metadata_to_dict(entry.federated)
            if entry.federated is not None
            else None
        ),
    }


def _snapshot_to_dict(snapshot: CatalogSnapshot) -> dict[str, Any]:
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "revision": snapshot.revision,
        "updated_at": snapshot.updated_at,
        "entries": [_entry_to_state(item) for item in snapshot.entries],
        "sources": [_source_state_to_dict(item) for item in snapshot.sources],
    }


def _snapshot_from_dict(value: Any) -> CatalogSnapshot:
    mapping = _strict_mapping(
        value,
        allowed={"schema_version", "revision", "updated_at", "entries", "sources"},
        field_name="integration catalog",
    )
    if mapping.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise CatalogStateError("unsupported integration catalog schema_version")
    revision = mapping.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise CatalogStateError("integration catalog revision is invalid")
    updated_at = mapping.get("updated_at")
    if updated_at is not None:
        _parse_timestamp(updated_at)
    raw_entries = mapping.get("entries")
    raw_sources = mapping.get("sources")
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_CATALOG_ENTRIES:
        raise CatalogStateError("integration catalog entries are invalid")
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_CATALOG_SOURCES:
        raise CatalogStateError("integration catalog sources are invalid")
    entries = tuple(_entry_from_state(item) for item in raw_entries)
    sources = tuple(_source_state_from_dict(item) for item in raw_sources)
    if len({item.catalog_id for item in entries}) != len(entries):
        raise CatalogStateError("integration catalog contains duplicate ids")
    if len({item.source_id for item in sources}) != len(sources):
        raise CatalogStateError("integration catalog contains duplicate sources")
    if entries != tuple(sorted(entries, key=_entry_sort_key)):
        raise CatalogStateError("integration catalog entries are not normalized")
    if sources != tuple(sorted(sources, key=lambda item: item.source_id)):
        raise CatalogStateError("integration catalog sources are not normalized")
    source_states = {item.source_id: item for item in sources}
    for entry in entries:
        state = source_states.get(entry.source_id)
        if state is None or state.source_type is not entry.source_type:
            raise CatalogStateError("catalog entry source state does not match")
    for state in sources:
        if state.entry_count != sum(
            item.source_id == state.source_id for item in entries
        ):
            raise CatalogStateError("catalog source entry_count does not match")
    if revision == 0 and (updated_at is not None or entries or sources):
        raise CatalogStateError("empty catalog revision contains state")
    if revision > 0 and updated_at is None:
        raise CatalogStateError("catalog updated_at is required")
    return CatalogSnapshot(
        revision=revision,
        updated_at=updated_at,
        entries=entries,
        sources=sources,
    )


def _entry_to_state(entry: CatalogEntry) -> dict[str, Any]:
    return {
        "catalog_id": entry.catalog_id,
        "source_id": entry.source_id,
        "source_type": entry.source_type.value,
        "package_id": entry.package_id,
        "version": entry.version,
        "immutable_ref": entry.immutable_ref,
        "content_hash": entry.content_hash,
        "status": entry.status.value,
        "pinned": entry.pinned,
        "source_present": entry.source_present,
        "install_authorized": entry.install_authorized,
        "first_seen_at": entry.first_seen_at,
        "last_seen_at": entry.last_seen_at,
        "package": (
            integration_package_to_dict(entry.package)
            if entry.package is not None
            else None
        ),
        "mcp_response": entry.mcp_response,
        "federated": (
            _federated_metadata_to_dict(entry.federated)
            if entry.federated is not None
            else None
        ),
    }


def _entry_from_state(value: Any) -> CatalogEntry:
    mapping = _strict_mapping(
        value,
        allowed={
            "catalog_id",
            "source_id",
            "source_type",
            "package_id",
            "version",
            "immutable_ref",
            "content_hash",
            "status",
            "pinned",
            "source_present",
            "install_authorized",
            "first_seen_at",
            "last_seen_at",
            "package",
            "mcp_response",
            "federated",
        },
        field_name="catalog entry",
    )
    package_payload = mapping.get("package")
    package = (
        integration_package_from_dict(package_payload)
        if package_payload is not None
        else None
    )
    return CatalogEntry(
        catalog_id=_required_text(mapping.get("catalog_id"), "catalog id"),
        source_id=_required_text(mapping.get("source_id"), "catalog source id"),
        source_type=_enum_value(
            CatalogSourceType, mapping.get("source_type"), "catalog source type"
        ),
        package_id=_required_text(mapping.get("package_id"), "catalog package id"),
        version=_required_text(mapping.get("version"), "catalog version"),
        immutable_ref=_optional_text(
            mapping.get("immutable_ref"), "catalog immutable ref"
        ),
        content_hash=_required_text(
            mapping.get("content_hash"), "catalog content hash"
        ),
        status=_enum_value(CatalogEntryStatus, mapping.get("status"), "catalog status"),
        pinned=_required_bool(mapping.get("pinned"), "catalog pinned"),
        source_present=_required_bool(
            mapping.get("source_present"), "catalog source_present"
        ),
        install_authorized=_required_bool(
            mapping.get("install_authorized"), "catalog install_authorized"
        ),
        first_seen_at=_required_text(
            mapping.get("first_seen_at"), "catalog first_seen_at"
        ),
        last_seen_at=_required_text(
            mapping.get("last_seen_at"), "catalog last_seen_at"
        ),
        package=package,
        mcp_response=mapping.get("mcp_response"),
        federated=(
            _federated_metadata_from_dict(mapping.get("federated"))
            if mapping.get("federated") is not None
            else None
        ),
    )


def _source_state_to_dict(state: CatalogSourceState) -> dict[str, Any]:
    return {
        "source_id": state.source_id,
        "source_type": state.source_type.value,
        "last_attempt_at": state.last_attempt_at,
        "last_success_at": state.last_success_at,
        "last_attempt_succeeded": state.last_attempt_succeeded,
        "complete": state.complete,
        "entry_count": state.entry_count,
        "cursor": state.cursor,
        "retry_count": state.retry_count,
        "next_retry_at": state.next_retry_at,
        "etag": state.etag,
        "freshness_expires_at": state.freshness_expires_at,
        "errors": [
            {
                "code": item.code,
                "source_id": item.source_id,
                "error_type": item.error_type,
                "occurred_at": item.occurred_at,
            }
            for item in state.errors
        ],
    }


def _source_state_from_dict(value: Any) -> CatalogSourceState:
    mapping = _strict_mapping(
        value,
        allowed={
            "source_id",
            "source_type",
            "last_attempt_at",
            "last_success_at",
            "last_attempt_succeeded",
            "complete",
            "entry_count",
            "cursor",
            "retry_count",
            "next_retry_at",
            "etag",
            "freshness_expires_at",
            "errors",
        },
        field_name="catalog source state",
    )
    errors = mapping.get("errors")
    if not isinstance(errors, list):
        raise ValueError("catalog source errors must be a list")
    return CatalogSourceState(
        source_id=_required_text(mapping.get("source_id"), "catalog source id"),
        source_type=_enum_value(
            CatalogSourceType, mapping.get("source_type"), "catalog source type"
        ),
        last_attempt_at=_required_text(
            mapping.get("last_attempt_at"), "catalog last_attempt_at"
        ),
        last_success_at=_optional_text(
            mapping.get("last_success_at"), "catalog last_success_at"
        ),
        last_attempt_succeeded=_required_bool(
            mapping.get("last_attempt_succeeded"), "catalog last_attempt_succeeded"
        ),
        complete=_required_bool(mapping.get("complete"), "catalog complete"),
        entry_count=_required_int(mapping.get("entry_count"), "catalog entry_count"),
        cursor=_optional_text(mapping.get("cursor"), "catalog source cursor"),
        retry_count=(
            _required_int(mapping.get("retry_count"), "catalog retry_count")
            if "retry_count" in mapping
            else 0
        ),
        next_retry_at=_optional_text(
            mapping.get("next_retry_at"), "catalog next_retry_at"
        ),
        etag=_optional_text(mapping.get("etag"), "catalog etag"),
        freshness_expires_at=_optional_text(
            mapping.get("freshness_expires_at"), "catalog freshness_expires_at"
        ),
        errors=tuple(_source_error_from_dict(item) for item in errors),
    )


def _federated_metadata_to_dict(
    metadata: FederatedCatalogMetadata,
) -> dict[str, Any]:
    return {
        "upstream_id": metadata.upstream_id,
        "canonical_package_id": metadata.canonical_package_id,
        "name": metadata.name,
        "component": metadata.component,
        "canonical_origin": metadata.canonical_origin,
        "detail_url": metadata.detail_url,
        "artifact_url": metadata.artifact_url,
        "curated": metadata.curated,
        "popularity": metadata.popularity,
        "upstream_audit": metadata.upstream_audit,
        "artifact_resolved": metadata.artifact_resolved,
        "source_present": metadata.source_present,
        "install_authorized": metadata.install_authorized,
        "observed_at": metadata.observed_at,
        "discovery_location": metadata.discovery_location,
        "immutable_ref": metadata.immutable_ref,
        "content_hash": metadata.content_hash,
        "relative_path": metadata.relative_path,
    }


def _federated_metadata_from_dict(value: Any) -> FederatedCatalogMetadata:
    mapping = _strict_mapping(
        value,
        allowed={
            "upstream_id",
            "canonical_package_id",
            "name",
            "component",
            "canonical_origin",
            "detail_url",
            "artifact_url",
            "curated",
            "popularity",
            "upstream_audit",
            "artifact_resolved",
            "source_present",
            "install_authorized",
            "observed_at",
            "discovery_location",
            "immutable_ref",
            "content_hash",
            "relative_path",
        },
        field_name="federated catalog metadata",
    )
    popularity = mapping.get("popularity")
    if popularity is not None:
        popularity = _required_int(popularity, "federated popularity")
    return FederatedCatalogMetadata(
        upstream_id=_required_text(mapping.get("upstream_id"), "federated upstream id"),
        canonical_package_id=_optional_text(
            mapping.get("canonical_package_id"), "federated canonical package id"
        ),
        name=_required_text(mapping.get("name"), "federated name"),
        component=_required_text(mapping.get("component"), "federated component"),
        canonical_origin=_required_text(
            mapping.get("canonical_origin"), "federated canonical origin"
        ),
        detail_url=_required_text(mapping.get("detail_url"), "federated detail URL"),
        artifact_url=_optional_text(
            mapping.get("artifact_url"), "federated artifact URL"
        ),
        curated=_required_bool(mapping.get("curated"), "federated curated"),
        popularity=popularity,
        upstream_audit=_optional_text(
            mapping.get("upstream_audit"), "federated upstream audit"
        ),
        artifact_resolved=_required_bool(
            mapping.get("artifact_resolved"), "federated artifact_resolved"
        ),
        source_present=_required_bool(
            mapping.get("source_present"), "federated source_present"
        ),
        install_authorized=_required_bool(
            mapping.get("install_authorized"), "federated install_authorized"
        ),
        observed_at=_optional_text(mapping.get("observed_at"), "federated observed_at"),
        discovery_location=_optional_text(
            mapping.get("discovery_location"), "federated discovery location"
        ),
        immutable_ref=_optional_text(
            mapping.get("immutable_ref"), "federated immutable ref"
        ),
        content_hash=_optional_text(
            mapping.get("content_hash"), "federated content hash"
        ),
        relative_path=_optional_text(
            mapping.get("relative_path"), "federated relative path"
        ),
    )


def _source_error_from_dict(value: Any) -> CatalogSourceError:
    mapping = _strict_mapping(
        value,
        allowed={"code", "source_id", "error_type", "occurred_at"},
        field_name="catalog source error",
    )
    return CatalogSourceError(
        code=_required_text(mapping.get("code"), "catalog source error code"),
        source_id=_required_text(mapping.get("source_id"), "catalog source id"),
        error_type=_required_text(mapping.get("error_type"), "catalog error type"),
        occurred_at=_required_text(
            mapping.get("occurred_at"), "catalog error timestamp"
        ),
    )


def _source_error(
    *,
    code: str,
    source_id: str,
    error_type: str,
    occurred_at: str,
) -> CatalogSourceError:
    safe_type = (
        re.sub(r"[^A-Za-z0-9._:+~-]", "_", error_type).lstrip("_")[:128] or "Error"
    )
    return CatalogSourceError(
        code=code,
        source_id=source_id,
        error_type=safe_type,
        occurred_at=occurred_at,
    )


def _bounded_errors(*errors: CatalogSourceError) -> tuple[CatalogSourceError, ...]:
    return tuple(errors[-MAX_CATALOG_SOURCE_ERRORS:])


__all__ = [name for name in globals() if not name.startswith("__")]
