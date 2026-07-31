"""Strict codecs for managed-agent plans, artifacts, activations, and locks."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.acp_registry import ACPDistributionKind
from gigaloom.contracts.agent_installation import (
    AgentActivationStatus,
    AgentActivationV1,
    AgentInstallPlanV1,
    AgentIntegrityPolicy,
    AgentLifecycleScriptPolicy,
    AgentLockV1,
    ManagedAgentArtifactV1,
    ManagedAgentStatus,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def agent_install_plan_to_dict(value: AgentInstallPlanV1) -> dict[str, Any]:
    """Serialize one immutable selected-distribution install plan."""
    return {
        "schema_version": value.schema_version,
        "plan_id": value.plan_id,
        "registry_id": value.registry_id,
        "entry_digest": value.entry_digest,
        "snapshot_digest": value.snapshot_digest,
        "local_agent_id": value.local_agent_id,
        "version": value.version,
        "platform": value.platform,
        "architecture": value.architecture,
        "distribution_kind": value.distribution_kind.value,
        "source": value.source,
        "package_or_archive": value.package_or_archive,
        "expected_integrity": value.expected_integrity,
        "command": value.command,
        "arguments": list(value.arguments),
        "environment": _environment_to_wire(value.environment),
        "network_origins": list(value.network_origins),
        "staging_root": value.staging_root,
        "managed_root": value.managed_root,
        "integrity_policy": value.integrity_policy.value,
        "lifecycle_script_policy": value.lifecycle_script_policy.value,
        "side_effects": list(value.side_effects),
        "confirmation_required": value.confirmation_required,
        "expires_at": value.expires_at.isoformat(),
    }


def agent_install_plan_from_dict(payload: Mapping[str, Any]) -> AgentInstallPlanV1:
    """Decode one strict install plan."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "plan_id",
            "registry_id",
            "entry_digest",
            "snapshot_digest",
            "local_agent_id",
            "version",
            "platform",
            "architecture",
            "distribution_kind",
            "source",
            "package_or_archive",
            "expected_integrity",
            "command",
            "arguments",
            "environment",
            "network_origins",
            "staging_root",
            "managed_root",
            "integrity_policy",
            "lifecycle_script_policy",
            "side_effects",
            "confirmation_required",
            "expires_at",
        },
        field_name="agent install plan",
    )
    return AgentInstallPlanV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        plan_id=_string(value["plan_id"], "plan_id"),
        registry_id=_string(value["registry_id"], "registry_id"),
        entry_digest=_string(value["entry_digest"], "entry_digest"),
        snapshot_digest=_string(value["snapshot_digest"], "snapshot_digest"),
        local_agent_id=_string(value["local_agent_id"], "local_agent_id"),
        version=_string(value["version"], "version"),
        platform=_string(value["platform"], "platform"),
        architecture=_string(value["architecture"], "architecture"),
        distribution_kind=_enum(
            ACPDistributionKind,
            value["distribution_kind"],
            "distribution_kind",
        ),
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
        environment=_environment_from_wire(value["environment"]),
        network_origins=_string_tuple(value["network_origins"], "network_origins"),
        staging_root=_string(value["staging_root"], "staging_root"),
        managed_root=_string(value["managed_root"], "managed_root"),
        integrity_policy=_enum(
            AgentIntegrityPolicy,
            value["integrity_policy"],
            "integrity_policy",
        ),
        lifecycle_script_policy=_enum(
            AgentLifecycleScriptPolicy,
            value["lifecycle_script_policy"],
            "lifecycle_script_policy",
        ),
        side_effects=_string_tuple(value["side_effects"], "side_effects"),
        confirmation_required=_boolean(
            value["confirmation_required"],
            "confirmation_required",
        ),
        expires_at=parse_timestamp(value["expires_at"], field_name="expires_at"),
    )


def managed_agent_artifact_to_dict(value: ManagedAgentArtifactV1) -> dict[str, Any]:
    """Serialize one immutable managed artifact."""
    return {
        "schema_version": value.schema_version,
        "install_id": value.install_id,
        "registry_id": value.registry_id,
        "local_agent_id": value.local_agent_id,
        "version": value.version,
        "distribution_kind": value.distribution_kind.value,
        "platform": value.platform,
        "artifact_digest": value.artifact_digest,
        "package_integrity": value.package_integrity,
        "lock_digest": value.lock_digest,
        "managed_root": value.managed_root,
        "executable_relative_path": value.executable_relative_path,
        "command": value.command,
        "arguments": list(value.arguments),
        "environment": _environment_to_wire(value.environment),
        "installed_at": value.installed_at.isoformat(),
        "status": value.status.value,
    }


def managed_agent_artifact_from_dict(
    payload: Mapping[str, Any],
) -> ManagedAgentArtifactV1:
    """Decode one strict managed artifact."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "install_id",
            "registry_id",
            "local_agent_id",
            "version",
            "distribution_kind",
            "platform",
            "artifact_digest",
            "package_integrity",
            "lock_digest",
            "managed_root",
            "executable_relative_path",
            "command",
            "arguments",
            "environment",
            "installed_at",
            "status",
        },
        field_name="managed agent artifact",
    )
    return ManagedAgentArtifactV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        install_id=_string(value["install_id"], "install_id"),
        registry_id=_string(value["registry_id"], "registry_id"),
        local_agent_id=_string(value["local_agent_id"], "local_agent_id"),
        version=_string(value["version"], "version"),
        distribution_kind=_enum(
            ACPDistributionKind,
            value["distribution_kind"],
            "distribution_kind",
        ),
        platform=_string(value["platform"], "platform"),
        artifact_digest=_string(value["artifact_digest"], "artifact_digest"),
        package_integrity=_optional_string(
            value["package_integrity"],
            "package_integrity",
        ),
        lock_digest=_string(value["lock_digest"], "lock_digest"),
        managed_root=_string(value["managed_root"], "managed_root"),
        executable_relative_path=_string(
            value["executable_relative_path"],
            "executable_relative_path",
        ),
        command=_string(value["command"], "command"),
        arguments=_string_tuple(value["arguments"], "arguments"),
        environment=_environment_from_wire(value["environment"]),
        installed_at=parse_timestamp(value["installed_at"], field_name="installed_at"),
        status=_enum(ManagedAgentStatus, value["status"], "status"),
    )


def agent_activation_to_dict(value: AgentActivationV1) -> dict[str, Any]:
    """Serialize one atomic agent activation."""
    return {
        "schema_version": value.schema_version,
        "activation_id": value.activation_id,
        "install_id": value.install_id,
        "previous_install_id": value.previous_install_id,
        "local_agent_id": value.local_agent_id,
        "profile_digest": value.profile_digest,
        "compatibility_observation_digest": value.compatibility_observation_digest,
        "activated_at": value.activated_at.isoformat(),
        "status": value.status.value,
        "atomic": value.atomic,
    }


def agent_activation_from_dict(payload: Mapping[str, Any]) -> AgentActivationV1:
    """Decode one strict atomic activation."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "activation_id",
            "install_id",
            "previous_install_id",
            "local_agent_id",
            "profile_digest",
            "compatibility_observation_digest",
            "activated_at",
            "status",
            "atomic",
        },
        field_name="agent activation",
    )
    return AgentActivationV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        activation_id=_string(value["activation_id"], "activation_id"),
        install_id=_string(value["install_id"], "install_id"),
        previous_install_id=_optional_string(
            value["previous_install_id"],
            "previous_install_id",
        ),
        local_agent_id=_string(value["local_agent_id"], "local_agent_id"),
        profile_digest=_string(value["profile_digest"], "profile_digest"),
        compatibility_observation_digest=_string(
            value["compatibility_observation_digest"],
            "compatibility_observation_digest",
        ),
        activated_at=parse_timestamp(value["activated_at"], field_name="activated_at"),
        status=_enum(AgentActivationStatus, value["status"], "status"),
        atomic=_boolean(value["atomic"], "atomic"),
    )


def agent_lock_to_dict(value: AgentLockV1) -> dict[str, Any]:
    """Serialize one machine-portable exact agent lock."""
    return {
        "schema_version": value.schema_version,
        "lock_id": value.lock_id,
        "registry_id": value.registry_id,
        "snapshot_digest": value.snapshot_digest,
        "entry_digest": value.entry_digest,
        "local_agent_id": value.local_agent_id,
        "version": value.version,
        "platform": value.platform,
        "architecture": value.architecture,
        "distribution_kind": value.distribution_kind.value,
        "artifact_digest": value.artifact_digest,
        "package_integrity": value.package_integrity,
        "command": value.command,
        "arguments": list(value.arguments),
        "environment": _environment_to_wire(value.environment),
        "generated_profile_digest": value.generated_profile_digest,
        "content_free": value.content_free,
        "lock_digest": value.lock_digest,
    }


def agent_lock_from_dict(payload: Mapping[str, Any]) -> AgentLockV1:
    """Decode and verify one machine-portable exact agent lock."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "lock_id",
            "registry_id",
            "snapshot_digest",
            "entry_digest",
            "local_agent_id",
            "version",
            "platform",
            "architecture",
            "distribution_kind",
            "artifact_digest",
            "package_integrity",
            "command",
            "arguments",
            "environment",
            "generated_profile_digest",
            "content_free",
            "lock_digest",
        },
        field_name="agent lock",
    )
    lock = AgentLockV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        lock_id=_string(value["lock_id"], "lock_id"),
        registry_id=_string(value["registry_id"], "registry_id"),
        snapshot_digest=_string(value["snapshot_digest"], "snapshot_digest"),
        entry_digest=_string(value["entry_digest"], "entry_digest"),
        local_agent_id=_string(value["local_agent_id"], "local_agent_id"),
        version=_string(value["version"], "version"),
        platform=_string(value["platform"], "platform"),
        architecture=_string(value["architecture"], "architecture"),
        distribution_kind=_enum(
            ACPDistributionKind,
            value["distribution_kind"],
            "distribution_kind",
        ),
        artifact_digest=_string(value["artifact_digest"], "artifact_digest"),
        package_integrity=_optional_string(
            value["package_integrity"],
            "package_integrity",
        ),
        command=_string(value["command"], "command"),
        arguments=_string_tuple(value["arguments"], "arguments"),
        environment=_environment_from_wire(value["environment"]),
        generated_profile_digest=_string(
            value["generated_profile_digest"],
            "generated_profile_digest",
        ),
        content_free=_boolean(value["content_free"], "content_free"),
    )
    supplied_digest = _string(value["lock_digest"], "lock_digest")
    if supplied_digest != lock.lock_digest:
        raise ValueError("agent lock digest does not match its fields")
    return lock


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _environment_to_wire(value: tuple[tuple[str, str], ...]) -> list[list[str]]:
    return [[key, item] for key, item in value]


def _environment_from_wire(value: object) -> tuple[tuple[str, str], ...]:
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
