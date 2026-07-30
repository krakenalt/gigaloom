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
from .validation import *  # noqa: F403


class CatalogSourceType(str, Enum):
    """Reviewed source families admitted to the N4 catalog."""

    LOCAL_PRIVATE = "local_private"
    OFFICIAL_MCP_REGISTRY = "official_mcp_registry"
    PROVIDER_MARKETPLACE = "provider_marketplace"
    GIT = "git"
    LOCAL = "local"
    FEDERATED_CATALOG = "federated_catalog"


class CatalogEntryStatus(str, Enum):
    """Lifecycle status retained independently from immutable content."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DELETED = "deleted"


class CatalogConflictError(RuntimeError):
    """Raised when a source attempts to replace an immutable catalog pin."""


class CatalogStateError(RuntimeError):
    """Raised when durable catalog state is corrupt or from a future schema."""


@dataclass(frozen=True, order=True)
class CatalogSourceError:
    """Bounded content-free source failure retained for diagnostics."""

    code: str
    source_id: str
    error_type: str
    occurred_at: str

    def __post_init__(self) -> None:
        _validate_identity(self.code, field_name="catalog source error code")
        _validate_identity(self.source_id, field_name="catalog source id")
        _validate_identity(self.error_type, field_name="catalog source error type")
        _parse_timestamp(self.occurred_at)


@dataclass(frozen=True)
class CatalogSourceState:
    """Last synchronization state for one independent catalog source."""

    source_id: str
    source_type: CatalogSourceType
    last_attempt_at: str
    last_success_at: str | None
    last_attempt_succeeded: bool
    complete: bool
    entry_count: int
    cursor: str | None = None
    retry_count: int = 0
    next_retry_at: str | None = None
    etag: str | None = None
    freshness_expires_at: str | None = None
    errors: tuple[CatalogSourceError, ...] = ()

    def __post_init__(self) -> None:
        _validate_identity(self.source_id, field_name="catalog source id")
        if not isinstance(self.source_type, CatalogSourceType):
            raise ValueError("catalog source type is invalid")
        _parse_timestamp(self.last_attempt_at)
        if self.last_success_at is not None:
            _parse_timestamp(self.last_success_at)
        if not isinstance(self.last_attempt_succeeded, bool):
            raise ValueError("catalog source success flag must be a boolean")
        if not isinstance(self.complete, bool):
            raise ValueError("catalog source completeness must be a boolean")
        if (
            isinstance(self.entry_count, bool)
            or not isinstance(self.entry_count, int)
            or self.entry_count < 0
            or self.entry_count > MAX_CATALOG_ENTRIES
        ):
            raise ValueError("catalog source entry_count is invalid")
        if self.cursor is not None:
            _validate_bounded_metadata(self.cursor, "catalog source cursor", 2_048)
        if (
            isinstance(self.retry_count, bool)
            or not isinstance(self.retry_count, int)
            or self.retry_count < 0
            or self.retry_count > 100
        ):
            raise ValueError("catalog source retry_count is invalid")
        if self.next_retry_at is not None:
            _parse_timestamp(self.next_retry_at)
        if self.etag is not None:
            _validate_bounded_metadata(self.etag, "catalog source etag", 512)
        if self.freshness_expires_at is not None:
            _parse_timestamp(self.freshness_expires_at)
        errors = tuple(self.errors)
        if len(errors) > MAX_CATALOG_SOURCE_ERRORS or any(
            not isinstance(item, CatalogSourceError) for item in errors
        ):
            raise ValueError("catalog source errors are invalid")
        object.__setattr__(self, "errors", errors)


@dataclass(frozen=True)
class FederatedCatalogMetadata:
    """Bounded discovery metadata retained without artifact authority."""

    upstream_id: str
    canonical_package_id: str | None
    name: str
    component: str
    canonical_origin: str
    detail_url: str
    artifact_url: str | None
    curated: bool
    popularity: int | None
    upstream_audit: str | None
    artifact_resolved: bool
    source_present: bool
    install_authorized: bool = False
    observed_at: str | None = None
    discovery_location: str | None = None
    immutable_ref: str | None = None
    content_hash: str | None = None
    relative_path: str | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.upstream_id, field_name="federated upstream id")
        if self.canonical_package_id is not None:
            _validate_mcp_name(self.canonical_package_id)
        _validate_bounded_metadata(self.name, "federated display name", 512)
        if self.component not in {"skill", "mcp"}:
            raise ValueError("federated component is invalid")
        _validate_https_origin(self.canonical_origin)
        _validate_https_url(self.detail_url)
        if self.artifact_url is not None:
            _validate_https_url(self.artifact_url)
        if not isinstance(self.curated, bool):
            raise ValueError("federated curated flag must be a boolean")
        if self.popularity is not None and (
            isinstance(self.popularity, bool)
            or not isinstance(self.popularity, int)
            or self.popularity < 0
        ):
            raise ValueError("federated popularity is invalid")
        if self.upstream_audit not in {
            None,
            "reported_approved",
            "reported_reviewed",
        }:
            raise ValueError("federated upstream audit is invalid")
        if not isinstance(self.artifact_resolved, bool) or not isinstance(
            self.source_present, bool
        ):
            raise ValueError("federated state flags must be booleans")
        if self.install_authorized is not False:
            raise ValueError("federated metadata cannot authorize installation")
        if self.observed_at is not None:
            _parse_timestamp(self.observed_at)
        if self.discovery_location is not None:
            _validate_bounded_metadata(
                self.discovery_location, "federated discovery location", 512
            )
        if self.immutable_ref is not None:
            _validate_immutable_ref(self.immutable_ref)
        if self.content_hash is not None:
            _validate_hash(self.content_hash, field_name="federated content hash")
        if self.relative_path is not None:
            _validate_relative_path(self.relative_path)


@dataclass(frozen=True)
class CatalogEntry:
    """One immutable package/server version plus mutable source visibility."""

    catalog_id: str
    source_id: str
    source_type: CatalogSourceType
    package_id: str
    version: str
    immutable_ref: str | None
    content_hash: str
    status: CatalogEntryStatus
    pinned: bool
    source_present: bool
    install_authorized: bool
    first_seen_at: str
    last_seen_at: str
    package: IntegrationPackage | None = None
    mcp_response: Mapping[str, Any] | None = None
    federated: FederatedCatalogMetadata | None = None

    def __post_init__(self) -> None:
        _validate_identity(self.catalog_id, field_name="catalog id")
        _validate_identity(self.source_id, field_name="catalog source id")
        if not isinstance(self.source_type, CatalogSourceType):
            raise ValueError("catalog source type is invalid")
        _validate_identity(self.package_id, field_name="catalog package id")
        _validate_identity(self.version, field_name="catalog version")
        if self.immutable_ref is not None:
            _validate_immutable_ref(self.immutable_ref)
        _validate_hash(self.content_hash, field_name="catalog content hash")
        if not isinstance(self.status, CatalogEntryStatus):
            raise ValueError("catalog entry status is invalid")
        if not isinstance(self.pinned, bool):
            raise ValueError("catalog pinned flag must be a boolean")
        if not isinstance(self.source_present, bool):
            raise ValueError("catalog source presence must be a boolean")
        if self.install_authorized is not False:
            raise ValueError("catalog entries cannot authorize installation")
        _parse_timestamp(self.first_seen_at)
        _parse_timestamp(self.last_seen_at)
        if _parse_timestamp(self.first_seen_at) > _parse_timestamp(self.last_seen_at):
            raise ValueError("catalog first_seen_at cannot follow last_seen_at")
        if self.catalog_id != _catalog_id(
            self.source_id,
            self.package_id,
            self.version,
        ):
            raise ValueError("catalog id does not match source identity")
        if (
            self.package is None
            and self.mcp_response is None
            and self.federated is None
        ):
            raise ValueError("catalog entry must contain a payload")
        if self.package is not None and self.mcp_response is not None:
            raise ValueError("catalog entry cannot contain package and MCP payloads")
        if self.federated is not None:
            if self.source_type not in {
                CatalogSourceType.FEDERATED_CATALOG,
                CatalogSourceType.GIT,
            }:
                raise ValueError(
                    "federated metadata requires a federated or reviewed Git source"
                )
            if self.federated.source_present != self.source_present:
                raise ValueError("federated source presence does not match entry")
            if self.federated.artifact_resolved != (self.package is not None):
                raise ValueError("federated artifact resolution does not match payload")
        if self.package is not None:
            if (
                self.package.id != self.package_id
                or self.package.version != self.version
            ):
                raise ValueError("catalog package identity does not match manifest")
            if self.package.immutable_ref != self.immutable_ref:
                raise ValueError("catalog immutable ref does not match manifest")
            if integration_package_semantic_hash(self.package) != self.content_hash:
                raise ValueError("catalog package content hash does not match")
            if self.status is not CatalogEntryStatus.ACTIVE:
                raise ValueError("manifest catalog entries must be active")
            if self.immutable_ref is None or self.pinned is not True:
                raise ValueError("manifest catalog entries require immutable pins")
            if self.source_type is CatalogSourceType.FEDERATED_CATALOG:
                if self.package.source_type is not IntegrationSourceType.GIT:
                    raise ValueError("federated packages must retain Git provenance")
            else:
                _validate_import_source(self.package.source_type, self.source_type)
        elif self.mcp_response is not None:
            if self.immutable_ref is None or self.pinned is not True:
                raise ValueError("MCP response entries require immutable pins")
            if (
                self.source_type is not CatalogSourceType.OFFICIAL_MCP_REGISTRY
                or self.source_id != OFFICIAL_MCP_REGISTRY_SOURCE_ID
            ):
                raise ValueError("MCP response entries require the official source")
            response = _normalize_mcp_response(self.mcp_response)
            server = response["server"]
            if server["name"] != self.package_id or server["version"] != self.version:
                raise ValueError("catalog MCP identity does not match response")
            if self.immutable_ref != f"{self.package_id}@{self.version}":
                raise ValueError("catalog MCP immutable ref does not match response")
            if _json_hash(server) != self.content_hash:
                raise ValueError("catalog MCP content hash does not match")
            if self.status is not _mcp_status(response):
                raise ValueError("catalog MCP status does not match response")
            object.__setattr__(self, "mcp_response", response)
        elif self.immutable_ref is not None or self.pinned:
            raise ValueError("discovery-only entries cannot claim immutable pins")

    @property
    def trust_decision(self) -> IntegrationTrustDecision:
        """Return package trust or the fail-closed raw-registry default."""
        if self.package is None:
            return IntegrationTrustDecision.REVIEW_REQUIRED
        return assess_integration_package(self.package).decision


@dataclass(frozen=True)
class CatalogSnapshot:
    """Complete local catalog snapshot loaded without upstream access."""

    revision: int
    updated_at: str | None
    entries: tuple[CatalogEntry, ...]
    sources: tuple[CatalogSourceState, ...]


@dataclass(frozen=True)
class CatalogSyncResult:
    """Result of one all-or-nothing source synchronization attempt."""

    success: bool
    fetched_count: int
    stored_count: int
    errors: tuple[CatalogSourceError, ...]


RegistryPageFetcher = Callable[..., Awaitable[Mapping[str, Any]]]

__all__ = [name for name in globals() if not name.startswith("__")]
