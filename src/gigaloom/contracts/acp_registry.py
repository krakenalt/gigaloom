"""Bounded ACP Registry snapshot and distribution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from gigaloom.contracts.operational_validation import (
    MAX_TUPLE_ITEMS,
    OPERATIONAL_SCHEMA_VERSION,
    canonical_digest,
    normalize_environment,
    normalize_tokens,
    validate_digest,
    validate_https_url,
    validate_identity,
    validate_network_origin,
    validate_optional_integrity,
    validate_schema_version,
    validate_text,
    validate_timestamp,
)


ACP_REGISTRY_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class ACPRegistrySourceKind(str, Enum):
    """Trusted source classification for a registry snapshot."""

    OFFICIAL = "official"
    MIRROR = "mirror"


class ACPDistributionKind(str, Enum):
    """Supported inert registry distribution metadata."""

    BINARY = "binary"
    NPX = "npx"
    UVX = "uvx"


@dataclass(frozen=True, slots=True)
class ACPDistributionV1:
    """One inert platform/package candidate from a registry entry."""

    kind: ACPDistributionKind
    platform: str
    architecture: str
    source: str
    package_or_archive: str
    expected_integrity: str | None
    command: str
    arguments: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    network_origins: tuple[str, ...]
    distribution_digest: str
    schema_version: int = ACP_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="ACP distribution")
        if not isinstance(self.kind, ACPDistributionKind):
            raise ValueError("ACP distribution kind is invalid")
        validate_identity(self.platform, field_name="ACP distribution platform")
        validate_identity(self.architecture, field_name="ACP distribution architecture")
        validate_https_url(self.source, field_name="ACP distribution source")
        validate_text(
            self.package_or_archive,
            field_name="ACP package or archive",
            max_chars=1_024,
        )
        validate_optional_integrity(
            self.expected_integrity,
            field_name="ACP expected integrity",
        )
        validate_text(
            self.command, field_name="ACP distribution command", max_chars=1_024
        )
        arguments = normalize_tokens(self.arguments, field_name="ACP arguments")
        environment = normalize_environment(self.environment)
        origins = _normalize_origins(self.network_origins)
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "environment", environment)
        object.__setattr__(self, "network_origins", origins)
        validate_digest(
            self.distribution_digest,
            field_name="ACP distribution digest",
        )
        if self.distribution_digest != _distribution_digest(self):
            raise ValueError("ACP distribution digest does not match its fields")


@dataclass(frozen=True, slots=True)
class ACPRegistrySnapshotV1:
    """Content-addressed metadata for one bounded registry snapshot."""

    source_url: str
    source_kind: ACPRegistrySourceKind
    fetched_at: datetime
    etag: str | None
    last_modified: str | None
    snapshot_digest: str
    entry_count: int
    entries_digest: str
    stale: bool
    schema_version: int = ACP_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="ACP registry snapshot")
        validate_https_url(self.source_url, field_name="ACP registry source URL")
        if not isinstance(self.source_kind, ACPRegistrySourceKind):
            raise ValueError("ACP registry source kind is invalid")
        validate_timestamp(self.fetched_at, field_name="ACP registry fetched_at")
        for value, label in (
            (self.etag, "ACP registry etag"),
            (self.last_modified, "ACP registry last_modified"),
        ):
            if value is not None:
                validate_text(value, field_name=label, max_chars=512)
        validate_digest(self.snapshot_digest, field_name="ACP snapshot digest")
        if (
            isinstance(self.entry_count, bool)
            or not isinstance(self.entry_count, int)
            or not 0 <= self.entry_count <= 100_000
        ):
            raise ValueError("ACP registry entry count is invalid")
        validate_digest(self.entries_digest, field_name="ACP entries digest")
        if not isinstance(self.stale, bool):
            raise ValueError("ACP registry stale flag must be boolean")


@dataclass(frozen=True, slots=True)
class ACPRegistryEntryV1:
    """Strict, execution-inert ACP Registry entry projection."""

    registry_id: str
    name: str
    version: str
    description: str
    repository: str | None
    website: str | None
    authors: tuple[str, ...]
    license: str
    icon_ref: str | None
    distributions: tuple[ACPDistributionV1, ...]
    entry_digest: str
    snapshot_digest: str
    schema_version: int = ACP_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="ACP registry entry")
        validate_identity(self.registry_id, field_name="ACP registry id")
        validate_text(self.name, field_name="ACP agent name", max_chars=256)
        validate_text(self.version, field_name="ACP agent version", max_chars=128)
        validate_text(
            self.description,
            field_name="ACP agent description",
            allow_empty=True,
            max_chars=4_096,
        )
        for value, label in (
            (self.repository, "ACP repository URL"),
            (self.website, "ACP website URL"),
            (self.icon_ref, "ACP icon reference"),
        ):
            if value is not None:
                validate_https_url(value, field_name=label)
        authors = _normalize_authors(self.authors)
        object.__setattr__(self, "authors", authors)
        validate_identity(self.license, field_name="ACP license")
        distributions = _normalize_distributions(self.distributions)
        object.__setattr__(self, "distributions", distributions)
        validate_digest(self.entry_digest, field_name="ACP entry digest")
        validate_digest(self.snapshot_digest, field_name="ACP snapshot digest")
        if self.entry_digest != _entry_digest(self):
            raise ValueError("ACP entry digest does not match its fields")


@runtime_checkable
class ACPRegistryPort(Protocol):
    """Public read-only registry source used by search and installation planning."""

    def snapshot(self, *, refresh: bool = False) -> ACPRegistrySnapshotV1:
        """Return one validated current or explicitly stale snapshot."""

    def entries(
        self,
        snapshot: ACPRegistrySnapshotV1,
    ) -> tuple[ACPRegistryEntryV1, ...]:
        """Return entries bound to the exact snapshot digest."""

    def entry(
        self,
        registry_id: str,
        *,
        snapshot_digest: str,
    ) -> ACPRegistryEntryV1 | None:
        """Return one exact entry without installing or executing it."""


def acp_distribution_digest(
    *,
    kind: ACPDistributionKind,
    platform: str,
    architecture: str,
    source: str,
    package_or_archive: str,
    expected_integrity: str | None,
    command: str,
    arguments: tuple[str, ...],
    environment: tuple[tuple[str, str], ...],
    network_origins: tuple[str, ...],
) -> str:
    """Compute the canonical digest required by `ACPDistributionV1`."""
    normalized_arguments = normalize_tokens(arguments, field_name="ACP arguments")
    normalized_environment = normalize_environment(environment)
    normalized_origins = _normalize_origins(network_origins)
    return canonical_digest(
        {
            "schema_version": ACP_REGISTRY_SCHEMA_VERSION,
            "kind": kind.value,
            "platform": platform,
            "architecture": architecture,
            "source": source,
            "package_or_archive": package_or_archive,
            "expected_integrity": expected_integrity,
            "command": command,
            "arguments": list(normalized_arguments),
            "environment": [[key, value] for key, value in normalized_environment],
            "network_origins": list(normalized_origins),
        }
    )


def acp_registry_entry_digest(
    *,
    registry_id: str,
    name: str,
    version: str,
    description: str,
    repository: str | None,
    website: str | None,
    authors: tuple[str, ...],
    license: str,
    icon_ref: str | None,
    distributions: tuple[ACPDistributionV1, ...],
) -> str:
    """Compute the canonical digest required by `ACPRegistryEntryV1`."""
    normalized_authors = _normalize_authors(authors)
    normalized_distributions = _normalize_distributions(distributions)
    return canonical_digest(
        {
            "schema_version": ACP_REGISTRY_SCHEMA_VERSION,
            "registry_id": registry_id,
            "name": name,
            "version": version,
            "description": description,
            "repository": repository,
            "website": website,
            "authors": list(normalized_authors),
            "license": license,
            "icon_ref": icon_ref,
            "distribution_digests": [
                item.distribution_digest for item in normalized_distributions
            ],
        }
    )


def _distribution_digest(value: ACPDistributionV1) -> str:
    return acp_distribution_digest(
        kind=value.kind,
        platform=value.platform,
        architecture=value.architecture,
        source=value.source,
        package_or_archive=value.package_or_archive,
        expected_integrity=value.expected_integrity,
        command=value.command,
        arguments=value.arguments,
        environment=value.environment,
        network_origins=value.network_origins,
    )


def _entry_digest(value: ACPRegistryEntryV1) -> str:
    return acp_registry_entry_digest(
        registry_id=value.registry_id,
        name=value.name,
        version=value.version,
        description=value.description,
        repository=value.repository,
        website=value.website,
        authors=value.authors,
        license=value.license,
        icon_ref=value.icon_ref,
        distributions=value.distributions,
    )


def _normalize_origins(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values or len(values) > 32:
        raise ValueError("ACP network origins must be a non-empty bounded tuple")
    normalized = tuple(
        validate_network_origin(value, field_name="ACP network origin")
        for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError("ACP network origins must be unique")
    return tuple(sorted(normalized))


def _normalize_authors(values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > 32:
        raise ValueError("ACP authors must be a bounded tuple")
    normalized = tuple(
        validate_text(value, field_name="ACP author", max_chars=256) for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError("ACP authors must be unique")
    return tuple(sorted(normalized))


def _normalize_distributions(values: object) -> tuple[ACPDistributionV1, ...]:
    if (
        not isinstance(values, tuple)
        or not values
        or len(values) > MAX_TUPLE_ITEMS
        or any(not isinstance(item, ACPDistributionV1) for item in values)
    ):
        raise ValueError("ACP distributions must be a non-empty bounded tuple")
    normalized = tuple(
        sorted(values, key=lambda item: (item.kind.value, item.distribution_digest))
    )
    digests = [item.distribution_digest for item in normalized]
    if len(set(digests)) != len(digests):
        raise ValueError("ACP distribution digests must be unique")
    return normalized
