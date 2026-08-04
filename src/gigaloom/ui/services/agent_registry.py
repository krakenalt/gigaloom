"""Browser-safe inventory projections for managed and local coding agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gigaloom.contracts import ACPDistributionV1, ACPRegistryEntryV1
from gigaloom.harnesses.agent_profiles.installations import (
    AgentRuntimeReadiness,
    AgentRuntimeService,
)
from gigaloom.harnesses.agent_profiles.registry.models import ACPRegistryCatalog


MAX_WEB_REGISTRY_RESULTS = 100


@dataclass(frozen=True, slots=True)
class AgentDistributionWebProjection:
    """One inert distribution row; it grants no installation authority."""

    kind: str
    platform: str
    architecture: str
    integrity: str
    package_or_archive: str


@dataclass(frozen=True, slots=True)
class AgentRegistryEntryWebProjection:
    """One searchable official-registry card."""

    registry_id: str
    name: str
    version: str
    description: str
    license: str
    repository: str | None
    website: str | None
    entry_digest: str
    platforms: tuple[str, ...]
    distribution_kinds: tuple[str, ...]
    integrity: str
    distributions: tuple[AgentDistributionWebProjection, ...]


@dataclass(frozen=True, slots=True)
class InstalledAgentWebProjection:
    """One installed immutable revision with current-registry comparison."""

    local_agent_id: str
    registry_id: str
    version: str
    install_id: str
    distribution_kind: str
    active: bool
    activation_status: str
    probe_state: str
    auth_required: bool
    update_available: bool
    readiness: AgentRuntimeReadiness


@dataclass(frozen=True, slots=True)
class LocalManifestWebProjection:
    """Existing advanced local registration shown beside managed installs."""

    agent_id: str
    display_name: str
    profile_digest: str
    source: str
    structured_route_ids: tuple[str, ...]
    native_available: bool


@dataclass(frozen=True, slots=True)
class AgentRegistryWebInventory:
    """Installed, registry, and local views bound to one registry revision."""

    snapshot_digest: str
    fetched_at: str
    stale: bool
    offline: bool
    from_cache: bool
    refresh_error_code: str | None
    explicitly_refreshed: bool
    registry_entries: tuple[AgentRegistryEntryWebProjection, ...]
    installed: tuple[InstalledAgentWebProjection, ...]
    local_manifests: tuple[LocalManifestWebProjection, ...]


class AgentRegistryWebService:
    """Use the shared runtime service for every browser inventory decision."""

    def __init__(
        self,
        runtime: AgentRuntimeService,
        *,
        local_manifests: Callable[[], tuple[LocalManifestWebProjection, ...]] = tuple,
    ) -> None:
        self._runtime = runtime
        self._local_manifests = local_manifests

    def inventory(
        self,
        query: str = "",
        *,
        refresh: bool = False,
        platform: str | None = None,
        distribution: str | None = None,
        integrity: str | None = None,
        license_name: str | None = None,
    ) -> AgentRegistryWebInventory:
        """Return one bounded locally filtered inventory and explicit cache state."""
        page = self._runtime.search(
            query.strip(),
            refresh=refresh,
            limit=MAX_WEB_REGISTRY_RESULTS,
        )
        entries = tuple(
            projection
            for entry in page.entries
            if _matches_filters(
                projection := _entry_projection(entry),
                platform=platform,
                distribution=distribution,
                integrity=integrity,
                license_name=license_name,
            )
        )
        installed = _installed_projections(self._runtime, page.catalog)
        if len(installed) > MAX_WEB_REGISTRY_RESULTS:
            raise ValueError("installed agent inventory exceeds its bound")
        local = tuple(sorted(self._local_manifests(), key=lambda item: item.agent_id))
        if len(local) > MAX_WEB_REGISTRY_RESULTS:
            raise ValueError("local agent manifest inventory exceeds its bound")
        snapshot = page.catalog.snapshot
        return AgentRegistryWebInventory(
            snapshot_digest=snapshot.snapshot_digest,
            fetched_at=snapshot.fetched_at.isoformat(),
            stale=snapshot.stale,
            offline=page.catalog.offline,
            from_cache=page.catalog.from_cache,
            refresh_error_code=page.catalog.refresh_error_code,
            explicitly_refreshed=refresh,
            registry_entries=entries,
            installed=installed,
            local_manifests=local,
        )


def _entry_projection(entry: ACPRegistryEntryV1) -> AgentRegistryEntryWebProjection:
    distributions = tuple(
        _distribution_projection(item) for item in entry.distributions
    )
    integrity_states = {item.integrity for item in distributions}
    integrity = (
        "verified"
        if integrity_states == {"verified"}
        else "unverified"
        if integrity_states == {"unverified"}
        else "mixed"
    )
    return AgentRegistryEntryWebProjection(
        registry_id=entry.registry_id,
        name=entry.name,
        version=entry.version,
        description=entry.description,
        license=entry.license,
        repository=entry.repository,
        website=entry.website,
        entry_digest=entry.entry_digest,
        platforms=tuple(
            sorted(
                {f"{item.platform}-{item.architecture}" for item in entry.distributions}
            )
        ),
        distribution_kinds=tuple(
            sorted({item.kind.value for item in entry.distributions})
        ),
        integrity=integrity,
        distributions=distributions,
    )


def _distribution_projection(
    value: ACPDistributionV1,
) -> AgentDistributionWebProjection:
    return AgentDistributionWebProjection(
        kind=value.kind.value,
        platform=value.platform,
        architecture=value.architecture,
        integrity="verified" if value.expected_integrity is not None else "unverified",
        package_or_archive=value.package_or_archive,
    )


def _matches_filters(
    entry: AgentRegistryEntryWebProjection,
    *,
    platform: str | None,
    distribution: str | None,
    integrity: str | None,
    license_name: str | None,
) -> bool:
    return (
        (platform is None or platform in entry.platforms)
        and (distribution is None or distribution in entry.distribution_kinds)
        and (integrity is None or integrity == entry.integrity)
        and (
            license_name is None or entry.license.casefold() == license_name.casefold()
        )
    )


def _installed_projections(
    runtime: AgentRuntimeService,
    catalog: ACPRegistryCatalog,
) -> tuple[InstalledAgentWebProjection, ...]:
    current_versions = {entry.registry_id: entry.version for entry in catalog.entries}
    return tuple(
        InstalledAgentWebProjection(
            local_agent_id=item.local_agent_id,
            registry_id=item.registry_id,
            version=item.version,
            install_id=item.install_id,
            distribution_kind=item.distribution_kind,
            active=item.active,
            activation_status=item.activation_status,
            probe_state=item.probe_state,
            auth_required=item.auth_required,
            update_available=(
                item.registry_id in current_versions
                and current_versions[item.registry_id] != item.version
            ),
            readiness=item.readiness,
        )
        for item in runtime.list()
    )


__all__ = [
    "AgentRegistryWebInventory",
    "AgentRegistryWebService",
    "LocalManifestWebProjection",
]
