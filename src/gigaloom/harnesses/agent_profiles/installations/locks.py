"""Byte-stable machine-portable managed ACP agent lock files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, cast

from gigaloom.contracts import AgentLockV1
from gigaloom.contracts.agent_installation_codec import (
    agent_lock_from_dict,
    agent_lock_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    read_json,
)
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAgentOnboardingResult,
)


@dataclass(frozen=True, slots=True)
class AgentLockSet:
    """Ordered exact locks and their aggregate byte-stable identity."""

    agents: tuple[AgentLockV1, ...]
    lockset_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.agents, tuple) or any(
            not isinstance(item, AgentLockV1) for item in self.agents
        ):
            raise ValueError("agent lock set entries are invalid")
        ordered = tuple(sorted(self.agents, key=lambda item: item.local_agent_id))
        if ordered != self.agents:
            raise ValueError("agent lock set entries must be ordered")
        ids = tuple(item.local_agent_id for item in self.agents)
        if len(set(ids)) != len(ids):
            raise ValueError("agent lock set local ids must be unique")
        expected = canonical_digest([item.lock_digest for item in self.agents])
        if self.lockset_digest != expected:
            raise ValueError("agent lock set digest does not match its entries")


def build_agent_lock_set(
    records: tuple[ManagedAgentOnboardingResult, ...],
) -> AgentLockSet:
    """Build exact portable locks from the selected active records only."""
    locks = tuple(
        sorted(
            (_lock_from_record(record) for record in records),
            key=lambda item: item.local_agent_id,
        )
    )
    return AgentLockSet(
        agents=locks,
        lockset_digest=canonical_digest([item.lock_digest for item in locks]),
    )


def write_agent_lock_file(path: str | Path, value: AgentLockSet) -> bytes:
    """Atomically write canonical JSON and return its exact bytes."""
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise ValueError("agent lock output cannot replace a symlink")
    payload = _lockset_to_dict(value)
    atomic_write_json(target, payload)
    return target.read_bytes()


def read_agent_lock_file(path: str | Path) -> AgentLockSet:
    """Strictly load and digest-check one managed ACP agent lock file."""
    value = read_json(Path(path))
    if set(value) != {"schema_version", "agents", "lockset_digest"}:
        raise ValueError("agent lock file fields are invalid")
    if value["schema_version"] != 1:
        raise ValueError("agent lock file schema version is unsupported")
    raw_agents = value["agents"]
    if not isinstance(raw_agents, list) or len(raw_agents) > 128:
        raise ValueError("agent lock file entries are invalid")
    agents = tuple(
        agent_lock_from_dict(_mapping(item)) for item in cast(list[object], raw_agents)
    )
    lockset_digest = value["lockset_digest"]
    if not isinstance(lockset_digest, str):
        raise ValueError("agent lock set digest is invalid")
    return AgentLockSet(agents=agents, lockset_digest=lockset_digest)


def _lock_from_record(record: ManagedAgentOnboardingResult) -> AgentLockV1:
    artifact = record.artifact
    receipt = record.receipt
    lock_fields = {
        "registry_id": artifact.registry_id,
        "snapshot_digest": receipt.snapshot_digest,
        "entry_digest": receipt.entry_digest,
        "local_agent_id": artifact.local_agent_id,
        "version": artifact.version,
        "platform": artifact.platform,
        "architecture": record.architecture,
        "distribution_kind": artifact.distribution_kind.value,
        "artifact_digest": artifact.artifact_digest,
        "package_integrity": artifact.package_integrity,
        "command": artifact.command,
        "arguments": list(artifact.arguments),
        "environment": [[key, value] for key, value in artifact.environment],
        "generated_profile_digest": record.profile.profile_digest,
    }
    lock_id = "lock-" + canonical_digest(lock_fields)[:24]
    return AgentLockV1(
        lock_id=lock_id,
        registry_id=artifact.registry_id,
        snapshot_digest=receipt.snapshot_digest,
        entry_digest=receipt.entry_digest,
        local_agent_id=artifact.local_agent_id,
        version=artifact.version,
        platform=artifact.platform,
        architecture=record.architecture,
        distribution_kind=artifact.distribution_kind,
        artifact_digest=artifact.artifact_digest,
        package_integrity=artifact.package_integrity,
        command=artifact.command,
        arguments=artifact.arguments,
        environment=artifact.environment,
        generated_profile_digest=record.profile.profile_digest,
    )


def _lockset_to_dict(value: AgentLockSet) -> dict[str, object]:
    return {
        "schema_version": 1,
        "agents": [agent_lock_to_dict(item) for item in value.agents],
        "lockset_digest": value.lockset_digest,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("agent lock entry must be an object")
    return cast(Mapping[str, object], value)
