"""Federated Skill search, detail, and provenance projections."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import os
from typing import Any


from gpt2giga_harness.federated_catalog import (
    FederatedCatalogCandidate,
    FederatedCatalogComponent,
    FederatedCatalogSource,
    NeuralDeepFederatedCatalogSource,
    SkillsShFederatedCatalogSource,
)
from gpt2giga_harness.integration_catalog import (
    FederatedCatalogMetadata,
)
from gpt2giga_harness.skills.catalog_proxy.client import SkillsCatalogProxyFetcher


class _LibraryFederatedMixin:
    """Read-only federated catalog behavior."""

    async def search(
        self,
        query: str,
        *,
        components: Sequence[str] = ("skill", "mcp"),
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search configured read-only catalogs and retain source failures."""
        query = query.strip()
        if not 2 <= len(query) <= 200:
            raise ValueError("catalog search query must contain 2 to 200 characters")
        if not 1 <= limit <= 100:
            raise ValueError("catalog search limit must be between 1 and 100")
        selected_components = {FederatedCatalogComponent(item) for item in components}
        if not selected_components:
            raise ValueError("catalog search requires at least one component")

        async def search_source(source: FederatedCatalogSource):
            if not selected_components.intersection(source.descriptor.components):
                return source.descriptor.source_id, (), None, source
            try:
                candidates = await source.search(query, limit=limit)
                return source.descriptor.source_id, candidates, None, source
            except Exception as exc:
                return (
                    source.descriptor.source_id,
                    (),
                    getattr(exc, "code", type(exc).__name__),
                    source,
                )

        results = await asyncio.gather(
            *(search_source(source) for source in self._federated_sources)
        )
        items: list[dict[str, Any]] = []
        sources = []
        for source_id, candidates, error_code, source in results:
            cache_status = getattr(source, "last_cache_status", "live")
            status = (
                "unavailable"
                if error_code is not None
                else "stale"
                if cache_status == "stale"
                else "ready"
            )
            sources.append(
                {
                    "id": source_id,
                    "status": status,
                    "reason_code": error_code
                    or getattr(source, "last_source_error", None),
                    "cache_status": cache_status,
                    "cache_age_seconds": getattr(
                        source, "last_cache_age_seconds", None
                    ),
                    "last_good": cache_status == "stale",
                }
            )
            items.extend(
                _federated_projection(item)
                for item in candidates
                if item.component in selected_components
            )
        sources.extend(
            {
                "id": source_id,
                "status": "configuration_required",
                "reason_code": "proxy_origin_missing",
                "cache_status": "unavailable",
                "cache_age_seconds": None,
                "last_good": False,
            }
            for source_id in self._unconfigured_source_ids
        )
        sources.sort(key=lambda item: str(item["id"]))
        items.sort(
            key=lambda item: (
                -int(item["curated"]),
                -int(item["popularity"] or 0),
                str(item["title"]).casefold(),
            )
        )
        return {
            "query": query,
            "items": items[:limit],
            "sources": sources,
            "install_authorized": False,
        }

    async def source_detail(
        self,
        source_id: str,
        upstream_id: str,
        *,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        """Resolve one selected source item to bounded provenance and file metadata."""
        source = next(
            (
                item
                for item in self._federated_sources
                if item.descriptor.source_id == source_id
            ),
            None,
        )
        if source is None:
            raise KeyError(source_id)
        candidate = await source.detail(upstream_id)
        audits = await source.audits(upstream_id) if include_audit else ()
        return {
            **_federated_projection(candidate),
            "provenance": _provenance_projection(candidate),
            "audits": [
                {
                    "provider": item.provider,
                    "status": item.status,
                    "audited_at": item.audited_at,
                    "risk_level": item.risk_level,
                }
                for item in audits
            ],
            "source_health": {
                "status": (
                    "stale"
                    if getattr(source, "last_cache_status", "live") == "stale"
                    else "ready"
                ),
                "cache_status": getattr(source, "last_cache_status", "live"),
                "cache_age_seconds": getattr(source, "last_cache_age_seconds", None),
                "reason_code": getattr(source, "last_source_error", None),
                "last_good": getattr(source, "last_cache_status", "live") == "stale",
            },
        }


def _default_sources() -> tuple[FederatedCatalogSource, ...]:
    sources: list[FederatedCatalogSource] = [NeuralDeepFederatedCatalogSource()]
    proxy_origin = os.environ.get("GIGA_SKILLS_PROXY_ORIGIN")
    if proxy_origin:
        sources.insert(
            0,
            SkillsShFederatedCatalogSource(
                hosted_fetch=SkillsCatalogProxyFetcher(proxy_origin)
            ),
        )
    return tuple(sources)


def _federated_projection(item: FederatedCatalogCandidate) -> dict[str, Any]:
    return {
        "id": f"remote:{item.source_id}:{item.upstream_id}",
        "source_id": item.source_id,
        "upstream_id": item.upstream_id,
        "title": item.name,
        "component": item.component.value,
        "artifact_url": item.provenance.artifact_url,
        "detail_url": item.provenance.detail_url,
        "curated": item.trust.curated,
        "popularity": item.trust.popularity,
        "upstream_audit": item.trust.upstream_audit,
        "canonical_origin": item.provenance.canonical_origin,
        "observed_at": item.provenance.observed_at,
        "discovery_location": f"{item.source_id}/{item.upstream_id}",
        "install_authorized": False,
    }


def _provenance_projection(item: FederatedCatalogCandidate) -> dict[str, Any]:
    return {
        "canonical_source": item.source_id,
        "upstream_id": item.upstream_id,
        "canonical_origin": item.provenance.canonical_origin,
        "repository_url": item.provenance.artifact_url,
        "artifact_url": item.provenance.artifact_url,
        "immutable_ref": item.immutable_ref,
        "content_hash": (
            item.immutable_ref.removeprefix("sha256:")
            if item.immutable_ref is not None
            else None
        ),
        "relative_path": item.provenance.relative_path,
        "file_paths": list(item.provenance.file_paths),
        "discovery_location": f"{item.source_id}/{item.upstream_id}",
        "detail_url": item.provenance.detail_url,
        "observed_at": item.provenance.observed_at,
        "trust": {
            "curated": item.trust.curated,
            "upstream_audit": item.trust.upstream_audit,
            "source_present": item.trust.source_present,
            "install_authorized": False,
        },
    }


def _federated_metadata_from_candidate(
    candidate: Mapping[str, Any],
) -> FederatedCatalogMetadata:
    provenance = candidate.get("source_provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("Git candidate provenance is invalid")
    trust = provenance.get("trust")
    if trust is not None and not isinstance(trust, Mapping):
        raise ValueError("Git candidate trust provenance is invalid")
    immutable_ref = provenance.get("immutable_ref")
    content_hash = provenance.get("content_hash")
    if not isinstance(immutable_ref, str) or not isinstance(content_hash, str):
        raise ValueError("Git candidate immutable provenance is incomplete")
    return FederatedCatalogMetadata(
        upstream_id=str(provenance["upstream_id"]),
        canonical_package_id=None,
        name=str(candidate["title"]),
        component="skill",
        canonical_origin=str(provenance["canonical_origin"]),
        detail_url=str(provenance["detail_url"]),
        artifact_url=str(provenance["artifact_url"]),
        curated=bool(trust.get("curated", False)) if trust is not None else False,
        popularity=None,
        upstream_audit=(
            str(trust["upstream_audit"])
            if trust is not None and trust.get("upstream_audit") is not None
            else None
        ),
        artifact_resolved=True,
        source_present=True,
        observed_at=str(provenance["observed_at"]),
        discovery_location=str(provenance["discovery_location"]),
        immutable_ref=immutable_ref,
        content_hash=content_hash,
        relative_path=(
            str(provenance["relative_path"])
            if provenance.get("relative_path") is not None
            else None
        ),
    )
