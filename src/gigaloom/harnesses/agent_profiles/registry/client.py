"""Explicit-refresh official ACP Registry client with honest cache fallback."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from gigaloom.contracts import (
    ACPRegistryEntryV1,
    ACPRegistryPort,
    ACPRegistrySnapshotV1,
)
from gigaloom.harnesses.agent_profiles.registry.cache import ACPRegistryCache
from gigaloom.harnesses.agent_profiles.registry.errors import (
    RegistryCacheError,
    RegistryNetworkError,
    RegistryResponseError,
    RegistryUnavailableError,
)
from gigaloom.harnesses.agent_profiles.registry.index import ACPRegistryIndex
from gigaloom.harnesses.agent_profiles.registry.models import ACPRegistryCatalog
from gigaloom.harnesses.agent_profiles.registry.schema import (
    MAX_REGISTRY_DOCUMENT_BYTES,
    OFFICIAL_ACP_REGISTRY_URL,
    decode_registry_document,
)
from gigaloom.harnesses.agent_profiles.registry.transport import (
    RegistryFetchRequest,
    RegistryFetchResponse,
    RegistryTransport,
    UrllibACPRegistryTransport,
)


class OfficialACPRegistryClient(ACPRegistryPort):
    """Read the canonical registry only after an explicit refresh action."""

    def __init__(
        self,
        *,
        cache: ACPRegistryCache,
        transport: RegistryTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._cache = cache
        self._transport = transport or UrllibACPRegistryTransport()
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._catalogs: dict[str, ACPRegistryCatalog] = {}
        self._indexes: dict[str, ACPRegistryIndex] = {}

    def catalog(self, *, refresh: bool = False) -> ACPRegistryCatalog:
        """Return cached metadata or perform one explicitly requested refresh."""
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("ACP registry client clock must be timezone-aware")
        try:
            cached = self._cache.load(now=now)
        except RegistryCacheError:
            if not refresh:
                raise
            cached = None
        if not refresh:
            if cached is None:
                raise RegistryUnavailableError(
                    "ACP registry has no cached snapshot; refresh is required"
                )
            return self._remember(cached)

        headers = {
            "Accept": "application/json",
            "User-Agent": "gigaloom-acp-registry/1",
        }
        if cached is not None:
            if cached.snapshot.etag is not None:
                headers["If-None-Match"] = cached.snapshot.etag
            if cached.snapshot.last_modified is not None:
                headers["If-Modified-Since"] = cached.snapshot.last_modified
        request = RegistryFetchRequest(
            url=OFFICIAL_ACP_REGISTRY_URL,
            headers=headers,
            max_bytes=MAX_REGISTRY_DOCUMENT_BYTES,
        )
        try:
            response = self._transport.fetch(request)
        except RegistryNetworkError as error:
            if cached is None:
                raise RegistryUnavailableError(
                    "ACP registry network is unavailable and there is no valid cached snapshot"
                ) from error
            return self._remember(
                cached.as_cached(
                    stale=True,
                    offline=True,
                    refresh_error_code="network_unavailable",
                )
            )
        return self._accept_response(response, cached=cached, fetched_at=now)

    def snapshot(self, *, refresh: bool = False) -> ACPRegistrySnapshotV1:
        """Implement the frozen registry port without hidden network access."""
        return self.catalog(refresh=refresh).snapshot

    def entries(
        self,
        snapshot: ACPRegistrySnapshotV1,
    ) -> tuple[ACPRegistryEntryV1, ...]:
        """Return entries only for the exact revision already observed."""
        catalog = self._catalogs.get(snapshot.snapshot_digest)
        if catalog is None:
            current = self.catalog()
            if current.snapshot.snapshot_digest != snapshot.snapshot_digest:
                raise ValueError(
                    "ACP registry snapshot is not the current cached revision"
                )
            catalog = current
        if catalog.snapshot.entries_digest != snapshot.entries_digest:
            raise ValueError(
                "ACP registry snapshot metadata does not match its revision"
            )
        return catalog.entries

    def entry(
        self,
        registry_id: str,
        *,
        snapshot_digest: str,
    ) -> ACPRegistryEntryV1 | None:
        """Return one exact inert entry without installing or executing it."""
        catalog = self._catalogs.get(snapshot_digest)
        if catalog is None:
            current = self.catalog()
            if current.snapshot.snapshot_digest != snapshot_digest:
                return None
            catalog = current
        return self._index(catalog).entries_by_id.get(registry_id)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> tuple[ACPRegistryEntryV1, ...]:
        """Search the local immutable index; this method never refreshes."""
        catalog = self.catalog()
        return self._index(catalog).search(query, limit=limit)

    def _accept_response(
        self,
        response: RegistryFetchResponse,
        *,
        cached: ACPRegistryCatalog | None,
        fetched_at: datetime,
    ) -> ACPRegistryCatalog:
        if response.final_url != OFFICIAL_ACP_REGISTRY_URL:
            raise RegistryResponseError("ACP registry redirected outside its exact URL")
        if response.status_code == 304:
            if cached is None:
                raise RegistryResponseError(
                    "ACP registry returned 304 without a valid cached snapshot"
                )
            updated = self._cache.mark_revalidated(
                cached,
                fetched_at=fetched_at,
                etag=response.header("etag") or cached.snapshot.etag,
                last_modified=(
                    response.header("last-modified") or cached.snapshot.last_modified
                ),
            )
            return self._remember(updated)
        if response.status_code != 200:
            raise RegistryResponseError(
                f"ACP registry returned unsupported HTTP {response.status_code}"
            )
        content_type = response.header("content-type")
        if content_type is None or content_type.split(";", 1)[
            0
        ].strip().lower() not in {
            "application/json",
            "application/schema+json",
        }:
            raise RegistryResponseError("ACP registry response is not JSON")
        catalog = decode_registry_document(
            response.body,
            fetched_at=fetched_at,
            etag=response.header("etag"),
            last_modified=response.header("last-modified"),
        )
        self._cache.store(response.body, catalog)
        return self._remember(catalog)

    def _remember(self, catalog: ACPRegistryCatalog) -> ACPRegistryCatalog:
        digest = catalog.snapshot.snapshot_digest
        self._catalogs[digest] = catalog
        while len(self._catalogs) > 8:
            evicted = next(iter(self._catalogs))
            self._catalogs.pop(evicted)
            self._indexes.pop(evicted, None)
        return catalog

    def _index(self, catalog: ACPRegistryCatalog) -> ACPRegistryIndex:
        digest = catalog.snapshot.snapshot_digest
        index = self._indexes.get(digest)
        if index is None:
            index = ACPRegistryIndex.build(catalog.entries)
            self._indexes[digest] = index
        return index
