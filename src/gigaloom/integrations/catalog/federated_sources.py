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
from .federated_transport import *  # noqa: F403
from .federated_validation import *  # noqa: F403


class _BaseFederatedSource:
    descriptor: FederatedSourceDescriptor

    def __init__(
        self, *, fetch: FederatedFetcher, now: Callable[[], datetime] | None
    ) -> None:
        self._fetch = fetch
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.last_good = FederatedCatalogSnapshot(
            revision=0, observed_at=None, items=()
        )
        self.health: FederatedSourceHealth | None = None
        self.last_cache_status = "live"
        self.last_cache_age_seconds: int | None = None
        self.last_source_error: str | None = None
        self._ephemeral_candidates: dict[str, FederatedCatalogCandidate] = {}

    async def refresh(
        self,
        *,
        components: Sequence[FederatedCatalogComponent] | None = None,
        page_size: int = 100,
    ) -> FederatedRefreshResult:
        selected = _validate_components(components, self.descriptor.components)
        _validate_limit(page_size, field="federated page_size")
        observed_at = _format_timestamp(self._now())
        try:
            incoming = await self._fetch_inventory(
                components=selected,
                page_size=page_size,
                observed_at=observed_at,
            )
            _reject_duplicate_candidates(incoming)
            if len(incoming) > MAX_FEDERATED_ENTRIES:
                raise _FederatedFailure("source.too_many_entries", "EntryLimit")
        except _FederatedFailure as exc:
            return self._failed_refresh(observed_at, exc)
        except Exception as exc:
            return self._failed_refresh(
                observed_at,
                _FederatedFailure("source.fetch_failed", type(exc).__name__),
            )

        incoming_by_id = {item.upstream_id: item for item in incoming}
        retained: list[FederatedCatalogCandidate] = []
        for item in self.last_good.items:
            if item.component not in selected:
                retained.append(item)
                continue
            if item.upstream_id not in incoming_by_id:
                retained.append(_with_source_presence(item, False))
        retained.extend(
            item
            for item in incoming
            if all(existing.upstream_id != item.upstream_id for existing in retained)
        )
        snapshot = FederatedCatalogSnapshot(
            revision=self.last_good.revision + 1,
            observed_at=observed_at,
            items=tuple(sorted(retained, key=_candidate_sort_key)),
        )
        self.last_good = snapshot
        self.health = FederatedSourceHealth(
            source_id=self.descriptor.source_id,
            last_attempt_at=observed_at,
            last_success_at=observed_at,
            last_attempt_succeeded=True,
            complete=True,
            cached_count=len(snapshot.items),
        )
        return FederatedRefreshResult(
            success=True, snapshot=snapshot, health=self.health
        )

    def _failed_refresh(
        self,
        observed_at: str,
        failure: _FederatedFailure,
    ) -> FederatedRefreshResult:
        self.health = FederatedSourceHealth(
            source_id=self.descriptor.source_id,
            last_attempt_at=observed_at,
            last_success_at=self.last_good.observed_at,
            last_attempt_succeeded=False,
            complete=False,
            cached_count=len(self.last_good.items),
            error_code=failure.code,
            error_type=_safe_error_type(failure.error_type),
        )
        return FederatedRefreshResult(
            success=False,
            snapshot=self.last_good,
            health=self.health,
        )

    async def _request_json(self, url: str, *, allow_not_found: bool = False) -> Any:
        expected_origin = self.descriptor.canonical_origin
        if _origin_for_url(url) != expected_origin:
            raise _FederatedFailure("source.origin_rejected", "OriginRejected")
        request = FederatedRequest(
            method="GET",
            url=url,
            headers={"Accept": "application/json"},
            timeout_seconds=FEDERATED_TIMEOUT_SECONDS,
            max_response_bytes=MAX_FEDERATED_RESPONSE_BYTES,
        )
        try:
            response = await self._fetch(request)
        except _FederatedFailure:
            raise
        except Exception as exc:
            raise _FederatedFailure("source.fetch_failed", type(exc).__name__) from exc
        if response.redirected or response.final_url != url:
            raise _FederatedFailure("source.redirect_rejected", "RedirectRejected")
        if response.status_code in {401, 403}:
            raise _FederatedFailure("source.auth_failed", "AuthenticationFailure")
        if response.status_code == 429:
            raise _FederatedFailure("source.rate_limited", "RateLimitFailure")
        if response.status_code == 404 and allow_not_found:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            raise _FederatedFailure("source.http_failed", "HTTPFailure")
        cache_status = _header(response.headers, "x-giga-cache-status")
        age = _header(response.headers, "age")
        self.last_cache_status = (
            cache_status if cache_status in {"fresh", "stale"} else "live"
        )
        self.last_cache_age_seconds = (
            int(age) if age is not None and age.isdigit() else None
        )
        self.last_source_error = _header(response.headers, "x-giga-source-error")
        if not isinstance(response.body, bytes):
            raise _FederatedFailure("source.invalid_payload", "ResponseBodyType")
        if len(response.body) > MAX_FEDERATED_RESPONSE_BYTES:
            raise _FederatedFailure("source.response_too_large", "ResponseLimit")
        content_type = next(
            (
                value
                for key, value in response.headers.items()
                if key.casefold() == "content-type"
            ),
            "application/json",
        )
        if not content_type.casefold().startswith("application/json"):
            raise _FederatedFailure("source.non_json_response", "ContentType")
        try:
            return json.loads(
                response.body.decode("utf-8"),
                object_pairs_hook=_unique_object,
            )
        except _FederatedFailure:
            raise
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise _FederatedFailure(
                "source.invalid_payload", type(exc).__name__
            ) from exc

    def _cached_detail(self, upstream_id: str) -> FederatedCatalogCandidate:
        _validate_id(upstream_id, "federated upstream id")
        candidate = next(
            (item for item in self.last_good.items if item.upstream_id == upstream_id),
            self._ephemeral_candidates.get(upstream_id),
        )
        if candidate is None or not candidate.source_present:
            raise KeyError(upstream_id)
        return candidate

    def _remember_candidates(
        self, items: Sequence[FederatedCatalogCandidate]
    ) -> tuple[FederatedCatalogCandidate, ...]:
        remembered = tuple(items)
        self._ephemeral_candidates = {
            item.upstream_id: item for item in remembered[:200]
        }
        return remembered

    async def _fetch_inventory(
        self,
        *,
        components: tuple[FederatedCatalogComponent, ...],
        page_size: int,
        observed_at: str,
    ) -> tuple[FederatedCatalogCandidate, ...]:
        raise NotImplementedError


class SkillsShFederatedCatalogSource(_BaseFederatedSource):
    """Metadata-only skills.sh boundary supplied by a Vercel-hosted fetcher."""

    descriptor: FederatedSourceDescriptor

    def __init__(
        self,
        *,
        hosted_fetch: FederatedFetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if hosted_fetch is None:
            raise ValueError("skills.sh requires an explicit hosted metadata fetcher")
        super().__init__(fetch=hosted_fetch, now=now)
        self._curated_ids: frozenset[str] | None = None

    async def _fetch_inventory(
        self,
        *,
        components: tuple[FederatedCatalogComponent, ...],
        page_size: int,
        observed_at: str,
    ) -> tuple[FederatedCatalogCandidate, ...]:
        if components != (FederatedCatalogComponent.SKILL,):
            raise _FederatedFailure(
                "source.unsupported_component", "UnsupportedComponent"
            )
        curated_ids = await self._fetch_curated_ids()
        candidates: list[FederatedCatalogCandidate] = []
        for page in range(MAX_FEDERATED_PAGES):
            url = f"{SKILLS_SH_ORIGIN}/api/v1/skills?page={page}&per_page={page_size}"
            payload = await self._request_json(url)
            items, has_more = _parse_skills_sh_page(
                payload,
                expected_page=page,
                page_size=page_size,
                observed_at=observed_at,
                curated_ids=curated_ids,
            )
            candidates.extend(items)
            if len(candidates) > MAX_FEDERATED_ENTRIES:
                raise _FederatedFailure("source.too_many_entries", "EntryLimit")
            if not has_more:
                return tuple(candidates)
        raise _FederatedFailure("source.pagination_incomplete", "PaginationLimit")

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> tuple[FederatedCatalogCandidate, ...]:
        query = _validate_query(query)
        _validate_limit(limit, field="federated search limit", maximum=200)
        curated_ids = (
            self._curated_ids
            if self._curated_ids is not None
            else await self._fetch_curated_ids()
        )
        url = (
            f"{SKILLS_SH_ORIGIN}/api/v1/skills/search?"
            f"q={urllib_parse.quote(query, safe='')}&limit={limit}"
        )
        payload = await self._request_json(url)
        items = _parse_skills_sh_search(
            payload,
            observed_at=_format_timestamp(self._now()),
            limit=limit,
            curated_ids=curated_ids,
        )
        _reject_duplicate_candidates(items)
        return self._remember_candidates(items)

    async def detail(self, upstream_id: str) -> FederatedCatalogCandidate:
        cached = self._cached_detail(upstream_id)
        url = f"{SKILLS_SH_ORIGIN}/api/v1/skills/{urllib_parse.quote(upstream_id, safe='/')}"
        payload = await self._request_json(url)
        digest, file_paths = _parse_skills_sh_detail(payload, expected_id=upstream_id)
        relative_path = next(
            (
                path
                for path in file_paths
                if path == "SKILL.md" or path.endswith("/SKILL.md")
            ),
            None,
        )
        return replace(
            cached,
            immutable_ref=f"sha256:{digest}",
            provenance=replace(
                cached.provenance,
                relative_path=relative_path,
                file_paths=file_paths,
            ),
        )

    async def audits(self, upstream_id: str) -> tuple[FederatedAuditProjection, ...]:
        self._cached_detail(upstream_id)
        url = (
            f"{SKILLS_SH_ORIGIN}/api/v1/skills/audit/"
            f"{urllib_parse.quote(upstream_id, safe='/')}"
        )
        payload = await self._request_json(url, allow_not_found=True)
        if payload is None:
            return ()
        return _parse_skills_sh_audits(payload, expected_id=upstream_id)

    async def resolve_artifact(self, upstream_id: str) -> FederatedArtifactResolution:
        detailed = await self.detail(upstream_id)
        if detailed.immutable_ref is None or detailed.provenance.artifact_url is None:
            return _unavailable_artifact(self.descriptor.source_id, upstream_id)
        return FederatedArtifactResolution(
            source_id=self.descriptor.source_id,
            upstream_id=upstream_id,
            available=True,
            immutable_ref=detailed.immutable_ref,
            artifact_url=detailed.provenance.artifact_url,
            reason_code=None,
            relative_path=detailed.provenance.relative_path,
        )

    async def _fetch_curated_ids(self) -> frozenset[str]:
        try:
            payload = await self._request_json(
                f"{SKILLS_SH_ORIGIN}/api/v1/skills/curated"
            )
            curated_ids = _parse_skills_sh_curated(payload)
        except _FederatedFailure as exc:
            self.last_source_error = exc.code
            curated_ids = frozenset()
        self._curated_ids = curated_ids
        return curated_ids


class NeuralDeepFederatedCatalogSource(_BaseFederatedSource):
    """Direct fixed-origin public-GET NeuralDeep discovery boundary."""

    descriptor: FederatedSourceDescriptor

    def __init__(
        self,
        *,
        fetch: FederatedFetcher | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(fetch=fetch or fetch_federated_json, now=now)

    async def _fetch_inventory(
        self,
        *,
        components: tuple[FederatedCatalogComponent, ...],
        page_size: int,
        observed_at: str,
    ) -> tuple[FederatedCatalogCandidate, ...]:
        del page_size
        candidates: list[FederatedCatalogCandidate] = []
        for component in components:
            url = f"{NEURALDEEP_ORIGIN}/skapi/skills?type={component.value}"
            payload = await self._request_json(url)
            candidates.extend(
                _parse_neuraldeep_items(
                    payload,
                    expected_component=component,
                    observed_at=observed_at,
                )
            )
        return tuple(candidates)

    async def search(
        self,
        query: str,
        *,
        limit: int = 50,
    ) -> tuple[FederatedCatalogCandidate, ...]:
        query = _validate_query(query)
        _validate_limit(limit, field="federated search limit", maximum=200)
        observed_at = _format_timestamp(self._now())
        candidates: list[FederatedCatalogCandidate] = []
        for component in self.descriptor.components:
            url = (
                f"{NEURALDEEP_ORIGIN}/skapi/skills?"
                f"q={urllib_parse.quote(query, safe='')}&type={component.value}"
            )
            payload = await self._request_json(url)
            candidates.extend(
                _parse_neuraldeep_items(
                    payload,
                    expected_component=component,
                    observed_at=observed_at,
                )
            )
        _reject_duplicate_candidates(candidates)
        return self._remember_candidates(
            tuple(sorted(candidates, key=_candidate_sort_key)[:limit])
        )

    async def detail(self, upstream_id: str) -> FederatedCatalogCandidate:
        return self._cached_detail(upstream_id)

    async def audits(self, upstream_id: str) -> tuple[FederatedAuditProjection, ...]:
        self._cached_detail(upstream_id)
        return ()

    async def resolve_artifact(self, upstream_id: str) -> FederatedArtifactResolution:
        self._cached_detail(upstream_id)
        return _unavailable_artifact(self.descriptor.source_id, upstream_id)


SkillsShFederatedCatalogSource.descriptor = FederatedSourceDescriptor(
    source_id=SKILLS_SH_SOURCE_ID,
    kind=FederatedSourceKind.HOSTED_METADATA,
    canonical_origin=SKILLS_SH_ORIGIN,
    components=(FederatedCatalogComponent.SKILL,),
    hosted_auth_required=True,
    immutable_reference_capable=True,
)
NeuralDeepFederatedCatalogSource.descriptor = FederatedSourceDescriptor(
    source_id=NEURALDEEP_SOURCE_ID,
    kind=FederatedSourceKind.PUBLIC_GET,
    canonical_origin=NEURALDEEP_ORIGIN,
    components=(FederatedCatalogComponent.SKILL, FederatedCatalogComponent.MCP),
    hosted_auth_required=False,
    immutable_reference_capable=False,
)

__all__ = [name for name in globals() if not name.startswith("__")]
