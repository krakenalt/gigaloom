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
from .federated_models import *  # noqa: F403
from .federated_validation import *  # noqa: F403


async def fetch_federated_json(request: FederatedRequest) -> FederatedHTTPResponse:
    """Execute one bounded direct GET without following redirects."""
    return await anyio.to_thread.run_sync(lambda: _read_federated_url(request))


def _read_federated_url(request: FederatedRequest) -> FederatedHTTPResponse:
    wire_request = urllib_request.Request(
        request.url,
        headers=dict(request.headers),
        method="GET",
    )
    opener = urllib_request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(wire_request, timeout=request.timeout_seconds) as response:
            body = response.read(request.max_response_bytes + 1)
            return FederatedHTTPResponse(
                status_code=response.status,
                final_url=response.geturl(),
                headers=dict(response.headers.items()),
                body=body,
            )
    except urllib_error.HTTPError as exc:
        body = exc.read(request.max_response_bytes + 1)
        return FederatedHTTPResponse(
            status_code=exc.code,
            final_url=request.url,
            headers=dict(exc.headers.items()) if exc.headers is not None else {},
            body=body,
            redirected=300 <= exc.code < 400,
        )


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_skills_sh_page(
    payload: Any,
    *,
    expected_page: int,
    page_size: int,
    observed_at: str,
    curated_ids: frozenset[str],
) -> tuple[tuple[FederatedCatalogCandidate, ...], bool]:
    mapping = _strict_mapping(payload, {"data", "pagination"})
    pagination = _strict_mapping(
        mapping.get("pagination"),
        {"page", "perPage", "total", "hasMore"},
    )
    page = _integer(pagination.get("page"), "skills.sh page")
    per_page = _integer(pagination.get("perPage"), "skills.sh perPage")
    total = _integer(pagination.get("total"), "skills.sh total")
    has_more = pagination.get("hasMore")
    if page != expected_page or per_page < 1 or per_page > page_size or total < 0:
        raise _FederatedFailure("source.pagination_incomplete", "PaginationState")
    if not isinstance(has_more, bool):
        raise _FederatedFailure("source.invalid_payload", "PaginationState")
    return (
        _parse_skills_sh_items(
            mapping.get("data"),
            observed_at=observed_at,
            curated_ids=curated_ids,
        ),
        has_more,
    )


def _parse_skills_sh_search(
    payload: Any,
    *,
    observed_at: str,
    limit: int,
    curated_ids: frozenset[str],
) -> tuple[FederatedCatalogCandidate, ...]:
    mapping = _strict_mapping(
        payload,
        {"data", "query", "searchType", "count", "durationMs"},
    )
    _validate_text(
        mapping.get("query"), "skills.sh query", maximum=MAX_FEDERATED_QUERY_LENGTH
    )
    _validate_text(mapping.get("searchType"), "skills.sh search type", maximum=32)
    count = _integer(mapping.get("count"), "skills.sh count")
    _integer(mapping.get("durationMs"), "skills.sh duration")
    items = _parse_skills_sh_items(
        mapping.get("data"),
        observed_at=observed_at,
        curated_ids=curated_ids,
    )
    if count != len(items) or len(items) > limit:
        raise _FederatedFailure("source.invalid_payload", "SearchCount")
    return items


def _parse_skills_sh_items(
    payload: Any,
    *,
    observed_at: str,
    curated_ids: frozenset[str],
) -> tuple[FederatedCatalogCandidate, ...]:
    if not isinstance(payload, list):
        raise _FederatedFailure("source.invalid_payload", "ItemList")
    if len(payload) > MAX_FEDERATED_ENTRIES:
        raise _FederatedFailure("source.too_many_entries", "EntryLimit")
    items = tuple(
        _parse_skills_sh_item(
            item,
            observed_at=observed_at,
            curated_ids=curated_ids,
        )
        for item in payload
    )
    _reject_duplicate_candidates(items)
    return items


def _parse_skills_sh_item(
    payload: Any, *, observed_at: str, curated_ids: frozenset[str]
) -> FederatedCatalogCandidate:
    mapping = _strict_mapping(payload, _SKILLS_SH_ITEM_FIELDS)
    upstream_id = _id(mapping.get("id"), "skills.sh id")
    slug = _id(mapping.get("slug"), "skills.sh slug")
    name = _text(mapping.get("name"), "skills.sh name")
    source = _id(mapping.get("source"), "skills.sh source")
    if upstream_id != f"{source}/{slug}":
        raise _FederatedFailure("source.invalid_payload", "IdentityMismatch")
    installs = _integer(mapping.get("installs"), "skills.sh installs")
    if installs < 0:
        raise _FederatedFailure("source.invalid_payload", "Popularity")
    source_type = mapping.get("sourceType")
    if source_type not in {"github", "well-known"}:
        raise _FederatedFailure("source.invalid_payload", "SourceType")
    install_url = mapping.get("installUrl")
    if install_url is not None:
        install_url = _https_url(install_url, "skills.sh install URL")
    detail_url = _https_url(mapping.get("url"), "skills.sh detail URL")
    if _origin_for_url(detail_url) != SKILLS_SH_ORIGIN:
        raise _FederatedFailure("source.origin_rejected", "DetailOrigin")
    duplicate = mapping.get("isDuplicate", False)
    if not isinstance(duplicate, bool):
        raise _FederatedFailure("source.invalid_payload", "DuplicateFlag")
    return _candidate(
        source_id=SKILLS_SH_SOURCE_ID,
        upstream_id=upstream_id,
        name=name,
        component=FederatedCatalogComponent.SKILL,
        source_present=True,
        observed_at=observed_at,
        detail_url=detail_url,
        artifact_url=install_url,
        curated=upstream_id in curated_ids,
        popularity=installs,
        upstream_audit="reported_reviewed" if not duplicate else None,
    )


def _parse_skills_sh_curated(payload: Any) -> frozenset[str]:
    mapping = _strict_mapping(
        payload,
        {"data", "totalOwners", "totalSkills", "generatedAt"},
    )
    owners = mapping.get("data")
    if not isinstance(owners, list) or len(owners) > 1_000:
        raise _FederatedFailure("source.invalid_payload", "CuratedOwners")
    curated_ids: set[str] = set()
    for owner in owners:
        owner_mapping = _strict_mapping(
            owner,
            {
                "owner",
                "totalInstalls",
                "featuredRepo",
                "featuredSkill",
                "skills",
            },
        )
        _text(owner_mapping.get("owner"), "skills.sh curated owner")
        _integer(owner_mapping.get("totalInstalls"), "skills.sh curated installs")
        _text(owner_mapping.get("featuredRepo"), "skills.sh featured repo")
        _text(owner_mapping.get("featuredSkill"), "skills.sh featured skill")
        skills = owner_mapping.get("skills")
        if not isinstance(skills, list):
            raise _FederatedFailure("source.invalid_payload", "CuratedSkills")
        for item in skills:
            item_mapping = _strict_mapping(item, _SKILLS_SH_ITEM_FIELDS)
            curated_ids.add(_id(item_mapping.get("id"), "skills.sh curated id"))
    _integer(mapping.get("totalOwners"), "skills.sh total owners")
    _integer(mapping.get("totalSkills"), "skills.sh total skills")
    _parse_timestamp(mapping.get("generatedAt"))
    return frozenset(curated_ids)


def _parse_skills_sh_detail(
    payload: Any, *, expected_id: str
) -> tuple[str, tuple[str, ...]]:
    mapping = _strict_mapping(
        payload,
        {"id", "source", "slug", "installs", "hash", "files"},
    )
    upstream_id = _id(mapping.get("id"), "skills.sh detail id")
    source = _id(mapping.get("source"), "skills.sh detail source")
    slug = _id(mapping.get("slug"), "skills.sh detail slug")
    _integer(mapping.get("installs"), "skills.sh detail installs")
    digest = mapping.get("hash")
    if upstream_id != expected_id or upstream_id != f"{source}/{slug}":
        raise _FederatedFailure("source.invalid_payload", "IdentityMismatch")
    if not isinstance(digest, str) or _HEX_HASH_RE.fullmatch(digest) is None:
        raise _FederatedFailure("source.invalid_payload", "ImmutableHash")
    raw_files = mapping.get("files")
    if raw_files is None:
        return digest, ()
    if not isinstance(raw_files, list) or len(raw_files) > 512:
        raise _FederatedFailure("source.invalid_payload", "FileTree")
    file_paths = []
    for item in raw_files:
        file_mapping = _strict_mapping(item, {"path"})
        path = file_mapping.get("path")
        try:
            _validate_relative_path(path)
        except ValueError as exc:
            raise _FederatedFailure("source.invalid_payload", "FilePath") from exc
        file_paths.append(path)
    if len(set(file_paths)) != len(file_paths):
        raise _FederatedFailure("source.invalid_payload", "DuplicateFilePath")
    return digest, tuple(file_paths)


def _parse_skills_sh_audits(
    payload: Any, *, expected_id: str
) -> tuple[FederatedAuditProjection, ...]:
    mapping = _strict_mapping(payload, {"id", "source", "slug", "audits"})
    upstream_id = _id(mapping.get("id"), "skills.sh audit id")
    source = _id(mapping.get("source"), "skills.sh audit source")
    slug = _id(mapping.get("slug"), "skills.sh audit slug")
    if upstream_id != expected_id or upstream_id != f"{source}/{slug}":
        raise _FederatedFailure("source.invalid_payload", "IdentityMismatch")
    audits = mapping.get("audits")
    if not isinstance(audits, list) or len(audits) > 32:
        raise _FederatedFailure("source.invalid_payload", "AuditList")
    result = []
    for item in audits:
        audit = _strict_mapping(
            item,
            {"provider", "slug", "status", "auditedAt", "riskLevel"},
        )
        result.append(
            FederatedAuditProjection(
                provider=_text(audit.get("provider"), "skills.sh audit provider"),
                status=_text(audit.get("status"), "skills.sh audit status"),
                audited_at=_text(audit.get("auditedAt"), "skills.sh audit timestamp"),
                risk_level=(
                    _text(audit.get("riskLevel"), "skills.sh audit risk")
                    if audit.get("riskLevel") is not None
                    else None
                ),
            )
        )
    return tuple(result)


def _parse_neuraldeep_items(
    payload: Any,
    *,
    expected_component: FederatedCatalogComponent,
    observed_at: str,
) -> tuple[FederatedCatalogCandidate, ...]:
    if not isinstance(payload, list):
        raise _FederatedFailure("source.invalid_payload", "ItemList")
    if len(payload) > MAX_FEDERATED_ENTRIES:
        raise _FederatedFailure("source.too_many_entries", "EntryLimit")
    items = tuple(
        _parse_neuraldeep_item(
            item,
            expected_component=expected_component,
            observed_at=observed_at,
        )
        for item in payload
    )
    _reject_duplicate_candidates(items)
    return items


def _parse_neuraldeep_item(
    payload: Any,
    *,
    expected_component: FederatedCatalogComponent,
    observed_at: str,
) -> FederatedCatalogCandidate:
    mapping = _strict_mapping(payload, _NEURALDEEP_ITEM_FIELDS)
    upstream_id = _id(mapping.get("id"), "NeuralDeep id")
    name = _text(mapping.get("name"), "NeuralDeep name")
    raw_component = mapping.get("type")
    try:
        component = FederatedCatalogComponent(raw_component)
    except (TypeError, ValueError) as exc:
        raise _FederatedFailure(
            "source.unsupported_component", "UnsupportedComponent"
        ) from exc
    if component is not expected_component:
        raise _FederatedFailure("source.unsupported_component", "ComponentMismatch")
    installs = _integer(mapping.get("installs"), "NeuralDeep installs")
    if installs < 0:
        raise _FederatedFailure("source.invalid_payload", "Popularity")
    status = mapping.get("status")
    if status not in {"approved", "deleted", "deprecated"}:
        raise _FederatedFailure("source.invalid_payload", "Status")
    source_present = status == "approved"
    featured = mapping.get("featured", False)
    if not isinstance(featured, bool):
        raise _FederatedFailure("source.invalid_payload", "FeaturedFlag")
    owner = mapping.get("owner")
    repo = mapping.get("repo")
    artifact_url: str | None = None
    if owner not in {None, ""} and repo not in {None, ""}:
        owner_id = _id(owner, "NeuralDeep owner")
        repo_id = _id(repo, "NeuralDeep repo")
        artifact_url = f"https://github.com/{owner_id}/{repo_id}"
    explicit_url = mapping.get("url")
    if artifact_url is None and explicit_url is not None:
        artifact_url = _https_url(explicit_url, "NeuralDeep artifact URL")
    relative_path = mapping.get("contentPath")
    if relative_path not in {None, ""}:
        try:
            _validate_relative_path(relative_path)
        except ValueError as exc:
            raise _FederatedFailure("source.invalid_payload", "ContentPath") from exc
    else:
        relative_path = None
    return _candidate(
        source_id=NEURALDEEP_SOURCE_ID,
        upstream_id=upstream_id,
        name=name,
        component=component,
        source_present=source_present,
        observed_at=observed_at,
        detail_url=(
            f"{NEURALDEEP_ORIGIN}/{component.value}/"
            f"{urllib_parse.quote(_neuraldeep_detail_slug(upstream_id, name, component), safe='')}"
        ),
        artifact_url=artifact_url,
        curated=featured or mapping.get("source") == "curated",
        popularity=installs,
        upstream_audit="reported_approved" if status == "approved" else None,
        relative_path=relative_path,
    )


def _neuraldeep_detail_slug(
    upstream_id: str,
    name: str,
    component: FederatedCatalogComponent,
) -> str:
    identity_slug = upstream_id.rsplit(":", 1)[-1].lower()
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identity_slug):
        return identity_slug
    name_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if component is FederatedCatalogComponent.MCP:
        name_slug = name_slug.removesuffix("-mcp")
    if not name_slug:
        raise _FederatedFailure("source.invalid_payload", "DetailSlug")
    return name_slug


def _candidate(
    *,
    source_id: str,
    upstream_id: str,
    name: str,
    component: FederatedCatalogComponent,
    source_present: bool,
    observed_at: str,
    detail_url: str,
    artifact_url: str | None,
    curated: bool,
    popularity: int,
    upstream_audit: str | None,
    relative_path: str | None = None,
) -> FederatedCatalogCandidate:
    return FederatedCatalogCandidate(
        source_id=source_id,
        upstream_id=upstream_id,
        name=name,
        component=component,
        source_present=source_present,
        immutable_ref=None,
        provenance=FederatedProvenance(
            source_id=source_id,
            upstream_id=upstream_id,
            canonical_origin=(
                SKILLS_SH_ORIGIN
                if source_id == SKILLS_SH_SOURCE_ID
                else NEURALDEEP_ORIGIN
            ),
            observed_at=observed_at,
            detail_url=detail_url,
            artifact_url=artifact_url,
            artifact_origin=_origin_for_url(artifact_url) if artifact_url else None,
            relative_path=relative_path,
            file_paths=(relative_path,) if relative_path is not None else (),
        ),
        trust=FederatedTrustProjection(
            source_present=source_present,
            curated=curated,
            popularity=popularity,
            upstream_audit=upstream_audit,
        ),
    )


def _with_source_presence(
    candidate: FederatedCatalogCandidate,
    source_present: bool,
) -> FederatedCatalogCandidate:
    return replace(
        candidate,
        source_present=source_present,
        trust=replace(candidate.trust, source_present=source_present),
    )


def _unavailable_artifact(
    source_id: str, upstream_id: str
) -> FederatedArtifactResolution:
    return FederatedArtifactResolution(
        source_id=source_id,
        upstream_id=upstream_id,
        available=False,
        immutable_ref=None,
        artifact_url=None,
        reason_code="immutable_reference_unavailable",
    )


__all__ = [name for name in globals() if not name.startswith("__")]
