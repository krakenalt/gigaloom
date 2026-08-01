"""Strict bounded codecs for ACP Registry snapshot contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.acp_registry import (
    ACPDistributionKind,
    ACPDistributionV1,
    ACPRegistryEntryV1,
    ACPRegistrySnapshotV1,
    ACPRegistrySourceKind,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def acp_distribution_to_dict(value: ACPDistributionV1) -> dict[str, Any]:
    """Serialize one inert registry distribution."""
    return {
        "schema_version": value.schema_version,
        "kind": value.kind.value,
        "platform": value.platform,
        "architecture": value.architecture,
        "source": value.source,
        "package_or_archive": value.package_or_archive,
        "expected_integrity": value.expected_integrity,
        "command": value.command,
        "arguments": list(value.arguments),
        "environment": [[key, item] for key, item in value.environment],
        "network_origins": list(value.network_origins),
        "distribution_digest": value.distribution_digest,
    }


def acp_distribution_from_dict(payload: Mapping[str, Any]) -> ACPDistributionV1:
    """Decode one strict inert registry distribution."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "kind",
            "platform",
            "architecture",
            "source",
            "package_or_archive",
            "expected_integrity",
            "command",
            "arguments",
            "environment",
            "network_origins",
            "distribution_digest",
        },
        field_name="ACP distribution",
    )
    return ACPDistributionV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        kind=_enum(ACPDistributionKind, value["kind"], "kind"),
        platform=_string(value["platform"], "platform"),
        architecture=_string(value["architecture"], "architecture"),
        source=_string(value["source"], "source"),
        package_or_archive=_string(
            value["package_or_archive"],
            "package_or_archive",
        ),
        expected_integrity=_optional_string(
            value["expected_integrity"],
            "expected_integrity",
        ),
        command=_string(value["command"], "command"),
        arguments=_string_tuple(value["arguments"], "arguments"),
        environment=_environment(value["environment"]),
        network_origins=_string_tuple(value["network_origins"], "network_origins"),
        distribution_digest=_string(
            value["distribution_digest"],
            "distribution_digest",
        ),
    )


def acp_registry_snapshot_to_dict(value: ACPRegistrySnapshotV1) -> dict[str, Any]:
    """Serialize one content-addressed registry snapshot."""
    return {
        "schema_version": value.schema_version,
        "source_url": value.source_url,
        "source_kind": value.source_kind.value,
        "fetched_at": value.fetched_at.isoformat(),
        "etag": value.etag,
        "last_modified": value.last_modified,
        "snapshot_digest": value.snapshot_digest,
        "entry_count": value.entry_count,
        "entries_digest": value.entries_digest,
        "stale": value.stale,
    }


def acp_registry_snapshot_from_dict(
    payload: Mapping[str, Any],
) -> ACPRegistrySnapshotV1:
    """Decode one strict registry snapshot."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "source_url",
            "source_kind",
            "fetched_at",
            "etag",
            "last_modified",
            "snapshot_digest",
            "entry_count",
            "entries_digest",
            "stale",
        },
        field_name="ACP registry snapshot",
    )
    return ACPRegistrySnapshotV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        source_url=_string(value["source_url"], "source_url"),
        source_kind=_enum(
            ACPRegistrySourceKind,
            value["source_kind"],
            "source_kind",
        ),
        fetched_at=parse_timestamp(value["fetched_at"], field_name="fetched_at"),
        etag=_optional_string(value["etag"], "etag"),
        last_modified=_optional_string(value["last_modified"], "last_modified"),
        snapshot_digest=_string(value["snapshot_digest"], "snapshot_digest"),
        entry_count=_integer(value["entry_count"], "entry_count"),
        entries_digest=_string(value["entries_digest"], "entries_digest"),
        stale=_boolean(value["stale"], "stale"),
    )


def acp_registry_entry_to_dict(value: ACPRegistryEntryV1) -> dict[str, Any]:
    """Serialize one strict registry entry."""
    return {
        "schema_version": value.schema_version,
        "registry_id": value.registry_id,
        "name": value.name,
        "version": value.version,
        "description": value.description,
        "repository": value.repository,
        "website": value.website,
        "authors": list(value.authors),
        "license": value.license,
        "icon_ref": value.icon_ref,
        "distributions": [
            acp_distribution_to_dict(item) for item in value.distributions
        ],
        "entry_digest": value.entry_digest,
        "snapshot_digest": value.snapshot_digest,
    }


def acp_registry_entry_from_dict(
    payload: Mapping[str, Any],
) -> ACPRegistryEntryV1:
    """Decode one strict registry entry."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "registry_id",
            "name",
            "version",
            "description",
            "repository",
            "website",
            "authors",
            "license",
            "icon_ref",
            "distributions",
            "entry_digest",
            "snapshot_digest",
        },
        field_name="ACP registry entry",
    )
    distributions = _object_array(value["distributions"], "distributions")
    return ACPRegistryEntryV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        registry_id=_string(value["registry_id"], "registry_id"),
        name=_string(value["name"], "name"),
        version=_string(value["version"], "version"),
        description=_string(value["description"], "description"),
        repository=_optional_string(value["repository"], "repository"),
        website=_optional_string(value["website"], "website"),
        authors=_string_tuple(value["authors"], "authors"),
        license=_string(value["license"], "license"),
        icon_ref=_optional_string(value["icon_ref"], "icon_ref"),
        distributions=tuple(acp_distribution_from_dict(item) for item in distributions),
        entry_digest=_string(value["entry_digest"], "entry_digest"),
        snapshot_digest=_string(value["snapshot_digest"], "snapshot_digest"),
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _object_array(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"{field_name} must be an array of objects")
    return tuple(cast(Mapping[str, Any], item) for item in value)


def _environment(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("environment must be an array")
    result: list[tuple[str, str]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(not isinstance(part, str) for part in item)
        ):
            raise ValueError("environment entries must be string pairs")
        result.append((cast(str, item[0]), cast(str, item[1])))
    return tuple(result)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(cast(str, item) for item in value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
