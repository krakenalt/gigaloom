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
from .federated_validation import *  # noqa: F403


class FederatedCatalogComponent(str, Enum):
    """Portable component families admitted by federation."""

    SKILL = "skill"
    MCP = "mcp"


class FederatedSourceKind(str, Enum):
    """Provider-neutral discovery source families, not marketplaces."""

    HOSTED_METADATA = "hosted_metadata"
    PUBLIC_GET = "public_get"


@dataclass(frozen=True)
class FederatedSourceDescriptor:
    """Static capabilities and ownership for one source boundary."""

    source_id: str
    kind: FederatedSourceKind
    canonical_origin: str
    components: tuple[FederatedCatalogComponent, ...]
    hosted_auth_required: bool
    immutable_reference_capable: bool
    install_authorized: bool = False

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "federated source id")
        if not isinstance(self.kind, FederatedSourceKind):
            raise ValueError("federated source kind is invalid")
        _canonical_https_origin(self.canonical_origin)
        if not self.components or any(
            not isinstance(item, FederatedCatalogComponent) for item in self.components
        ):
            raise ValueError("federated source components are invalid")
        if not isinstance(self.hosted_auth_required, bool):
            raise ValueError("hosted auth requirement must be boolean")
        if not isinstance(self.immutable_reference_capable, bool):
            raise ValueError("immutable reference capability must be boolean")
        if self.install_authorized is not False:
            raise ValueError("federated sources cannot authorize installation")


@dataclass(frozen=True)
class FederatedProvenance:
    """Content-free upstream identity retained for one candidate."""

    source_id: str
    upstream_id: str
    canonical_origin: str
    observed_at: str
    detail_url: str
    artifact_url: str | None
    artifact_origin: str | None
    relative_path: str | None = None
    file_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "federated source id")
        _validate_id(self.upstream_id, "federated upstream id")
        _canonical_https_origin(self.canonical_origin)
        _parse_timestamp(self.observed_at)
        _validate_https_url(self.detail_url)
        if self.artifact_url is None:
            if self.artifact_origin is not None:
                raise ValueError("artifact origin requires an artifact URL")
        else:
            origin = _origin_for_url(self.artifact_url)
            if self.artifact_origin != origin:
                raise ValueError("artifact origin does not match artifact URL")
        if self.relative_path is not None:
            _validate_relative_path(self.relative_path)
        file_paths = tuple(self.file_paths)
        if len(file_paths) > 512:
            raise ValueError("federated file tree is too large")
        for path in file_paths:
            _validate_relative_path(path)
        if len(set(file_paths)) != len(file_paths):
            raise ValueError("federated file tree contains duplicate paths")
        if self.relative_path is not None and self.relative_path not in file_paths:
            raise ValueError("federated relative path is absent from the file tree")
        object.__setattr__(self, "file_paths", file_paths)


@dataclass(frozen=True)
class FederatedTrustProjection:
    """Bounded upstream claims that never imply installation authority."""

    source_present: bool
    curated: bool
    popularity: int | None
    upstream_audit: str | None
    install_authorized: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source_present, bool) or not isinstance(
            self.curated, bool
        ):
            raise ValueError("federated trust flags must be boolean")
        if self.popularity is not None and (
            isinstance(self.popularity, bool)
            or not isinstance(self.popularity, int)
            or self.popularity < 0
        ):
            raise ValueError("federated popularity is invalid")
        if self.upstream_audit not in {None, "reported_approved", "reported_reviewed"}:
            raise ValueError("federated audit projection is invalid")
        if self.install_authorized is not False:
            raise ValueError("federated trust cannot authorize installation")


@dataclass(frozen=True)
class FederatedCatalogCandidate:
    """One bounded Skills or MCP discovery candidate."""

    source_id: str
    upstream_id: str
    name: str
    component: FederatedCatalogComponent
    source_present: bool
    immutable_ref: str | None
    provenance: FederatedProvenance
    trust: FederatedTrustProjection

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "federated source id")
        _validate_id(self.upstream_id, "federated upstream id")
        _validate_text(self.name, "federated candidate name")
        if not isinstance(self.component, FederatedCatalogComponent):
            raise ValueError("federated candidate component is invalid")
        if not isinstance(self.source_present, bool):
            raise ValueError("federated source presence must be boolean")
        if self.immutable_ref is not None and not self.immutable_ref.startswith(
            "sha256:"
        ):
            raise ValueError("federated immutable ref is invalid")
        if self.provenance.source_id != self.source_id:
            raise ValueError("federated provenance source does not match")
        if self.provenance.upstream_id != self.upstream_id:
            raise ValueError("federated provenance identity does not match")
        if self.trust.source_present != self.source_present:
            raise ValueError("federated trust presence does not match")
        if self.trust.install_authorized is not False:
            raise ValueError("federated candidate cannot authorize installation")


@dataclass(frozen=True)
class FederatedArtifactResolution:
    """Immutable artifact-reference resolution without downloading content."""

    source_id: str
    upstream_id: str
    available: bool
    immutable_ref: str | None
    artifact_url: str | None
    reason_code: str | None
    relative_path: str | None = None
    install_authorized: bool = False

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "federated source id")
        _validate_id(self.upstream_id, "federated upstream id")
        if not isinstance(self.available, bool):
            raise ValueError("artifact availability must be boolean")
        if self.available:
            if self.immutable_ref is None or self.artifact_url is None:
                raise ValueError(
                    "available artifact resolution requires an immutable ref"
                )
            if self.reason_code is not None:
                raise ValueError(
                    "available artifact resolution cannot include a reason"
                )
            _validate_sha256_ref(self.immutable_ref)
            _validate_https_url(self.artifact_url)
            if self.relative_path is not None:
                _validate_relative_path(self.relative_path)
        elif (
            self.immutable_ref is not None
            or self.artifact_url is not None
            or self.reason_code != "immutable_reference_unavailable"
            or self.relative_path is not None
        ):
            raise ValueError("unavailable artifact resolution is invalid")
        if self.install_authorized is not False:
            raise ValueError("artifact resolution cannot authorize installation")


@dataclass(frozen=True)
class FederatedCatalogSnapshot:
    """Last complete in-process source read."""

    revision: int
    observed_at: str | None
    items: tuple[FederatedCatalogCandidate, ...]


@dataclass(frozen=True)
class FederatedSourceHealth:
    """Bounded content-free source status."""

    source_id: str
    last_attempt_at: str
    last_success_at: str | None
    last_attempt_succeeded: bool
    complete: bool
    cached_count: int
    error_code: str | None = None
    error_type: str | None = None
    install_authorized: bool = False

    def __post_init__(self) -> None:
        _validate_id(self.source_id, "federated source id")
        _parse_timestamp(self.last_attempt_at)
        if self.last_success_at is not None:
            _parse_timestamp(self.last_success_at)
        if not isinstance(self.last_attempt_succeeded, bool) or not isinstance(
            self.complete, bool
        ):
            raise ValueError("federated source health flags must be boolean")
        if (
            isinstance(self.cached_count, bool)
            or not isinstance(self.cached_count, int)
            or self.cached_count < 0
            or self.cached_count > MAX_FEDERATED_ENTRIES
        ):
            raise ValueError("federated cached count is invalid")
        if (self.error_code is None) != (self.error_type is None):
            raise ValueError("federated source error fields must be paired")
        if self.error_code is not None:
            _validate_id(self.error_code, "federated error code")
            _validate_id(self.error_type, "federated error type")
        if self.install_authorized is not False:
            raise ValueError("source health cannot authorize installation")


@dataclass(frozen=True)
class FederatedRefreshResult:
    """Result of an all-pages-before-publish source refresh."""

    success: bool
    snapshot: FederatedCatalogSnapshot
    health: FederatedSourceHealth


@dataclass(frozen=True)
class FederatedRequest:
    """Fixed-origin bounded metadata request passed to an injected fetcher."""

    method: str
    url: str
    headers: Mapping[str, str]
    timeout_seconds: float
    max_response_bytes: int
    allow_loopback_http: bool = False

    def __post_init__(self) -> None:
        if self.method != "GET":
            raise ValueError("federated sources are read-only")
        if self.allow_loopback_http:
            _validate_loopback_http_url(self.url)
        else:
            _validate_https_url(self.url)
        if dict(self.headers) != {"Accept": "application/json"}:
            raise ValueError("federated request headers are invalid")
        if self.timeout_seconds != FEDERATED_TIMEOUT_SECONDS:
            raise ValueError("federated request timeout is invalid")
        if self.max_response_bytes != MAX_FEDERATED_RESPONSE_BYTES:
            raise ValueError("federated response bound is invalid")


@dataclass(frozen=True)
class FederatedHTTPResponse:
    """Transport-neutral JSON response metadata."""

    status_code: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes
    redirected: bool = False


FederatedFetcher = Callable[[FederatedRequest], Awaitable[FederatedHTTPResponse]]


@dataclass(frozen=True)
class FederatedAuditProjection:
    """Bounded content-free security-audit metadata for one candidate."""

    provider: str
    status: str
    audited_at: str
    risk_level: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.provider, "federated audit provider", maximum=128)
        if self.status not in {"pass", "warn", "fail"}:
            raise ValueError("federated audit status is invalid")
        _parse_timestamp(self.audited_at)
        if self.risk_level not in {
            None,
            "NONE",
            "LOW",
            "MEDIUM",
            "HIGH",
            "CRITICAL",
        }:
            raise ValueError("federated audit risk level is invalid")


class FederatedCatalogSource(Protocol):
    """Versioned bounded source contract shared by all federation adapters."""

    descriptor: FederatedSourceDescriptor
    last_good: FederatedCatalogSnapshot
    health: FederatedSourceHealth | None

    async def refresh(
        self,
        *,
        components: Sequence[FederatedCatalogComponent] | None = None,
        page_size: int = 100,
    ) -> FederatedRefreshResult: ...

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> tuple[FederatedCatalogCandidate, ...]: ...

    async def detail(self, upstream_id: str) -> FederatedCatalogCandidate: ...

    async def audits(
        self, upstream_id: str
    ) -> tuple[FederatedAuditProjection, ...]: ...

    async def resolve_artifact(
        self, upstream_id: str
    ) -> FederatedArtifactResolution: ...


class _FederatedFailure(RuntimeError):
    def __init__(self, code: str, error_type: str) -> None:
        super().__init__(code)
        self.code = code
        self.error_type = error_type


class _SchemaDrift(_FederatedFailure):
    def __init__(self) -> None:
        super().__init__("source.schema_drift", "SchemaDrift")


def _reject_duplicate_candidates(items: Sequence[FederatedCatalogCandidate]) -> None:
    identities = [item.upstream_id for item in items]
    if len(set(identities)) != len(identities):
        raise _FederatedFailure("source.duplicate_entry", "DuplicateEntry")


def _validate_components(
    requested: Sequence[FederatedCatalogComponent] | None,
    supported: tuple[FederatedCatalogComponent, ...],
) -> tuple[FederatedCatalogComponent, ...]:
    if requested is None:
        return supported
    selected = tuple(requested)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("federated components are invalid")
    if any(not isinstance(item, FederatedCatalogComponent) for item in selected):
        raise _FederatedFailure("source.unsupported_component", "UnsupportedComponent")
    if any(item not in supported for item in selected):
        raise _FederatedFailure("source.unsupported_component", "UnsupportedComponent")
    return tuple(item for item in supported if item in selected)


def _candidate_sort_key(
    candidate: FederatedCatalogCandidate,
) -> tuple[str, str, str]:
    return (candidate.component.value, candidate.name.casefold(), candidate.upstream_id)


__all__ = [name for name in globals() if not name.startswith("__")]
