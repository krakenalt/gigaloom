"""Atomic managed-agent active/previous artifact pointers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

from gigaloom.contracts import (
    AgentActivationStatus,
    AgentActivationV1,
    ManagedAgentArtifactV1,
)
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_from_dict,
    agent_activation_to_dict,
    managed_agent_artifact_from_dict,
    managed_agent_artifact_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.errors import AgentInstallError
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
    require_within,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


class ManagedAgentActivationStore:
    """Publish and roll back one active pointer without mutating artifacts."""

    def __init__(self, data_root: str | Path) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._state_root = self._data_root / "agents" / "state"

    def activate(
        self,
        artifact: ManagedAgentArtifactV1,
        *,
        profile_digest: str,
        compatibility_observation_digest: str,
        status: AgentActivationStatus,
        activated_at: datetime,
    ) -> AgentActivationV1:
        """Atomically replace current while retaining one rollback artifact."""
        if status not in {AgentActivationStatus.READY, AgentActivationStatus.DEGRADED}:
            raise AgentInstallError("agent_activation_status_not_runnable")
        require_within(
            Path(artifact.managed_root),
            self._data_root / "agents" / "registry",
            reason_code="managed_artifact_outside_authority",
        )
        if not (Path(artifact.managed_root) / ".artifact.json").is_file():
            raise AgentInstallError("managed_artifact_metadata_missing")
        ensure_private_directory(self._state_root)
        pointer = self._pointer_path(artifact.local_agent_id)
        with registry_cache_lock(self._state_root / ".activation.lock"):
            previous = self._read_pointer(pointer) if pointer.exists() else None
            previous_artifact = previous[0] if previous is not None else None
            activation = AgentActivationV1(
                activation_id=_activation_id(
                    artifact.install_id,
                    previous_artifact.install_id if previous_artifact else None,
                    profile_digest,
                    compatibility_observation_digest,
                ),
                install_id=artifact.install_id,
                previous_install_id=(
                    previous_artifact.install_id if previous_artifact else None
                ),
                local_agent_id=artifact.local_agent_id,
                profile_digest=profile_digest,
                compatibility_observation_digest=compatibility_observation_digest,
                activated_at=activated_at,
                status=status,
            )
            atomic_write_json(
                pointer,
                {
                    "schema_version": 1,
                    "current_artifact": managed_agent_artifact_to_dict(artifact),
                    "previous_artifact": (
                        managed_agent_artifact_to_dict(previous_artifact)
                        if previous_artifact is not None
                        else None
                    ),
                    "activation": agent_activation_to_dict(activation),
                },
            )
            return activation

    def rollback(
        self,
        local_agent_id: str,
        *,
        profile_digest: str,
        compatibility_observation_digest: str,
        activated_at: datetime,
    ) -> AgentActivationV1:
        """Atomically swap the previous artifact back into the current pointer."""
        pointer = self._pointer_path(local_agent_id)
        with registry_cache_lock(self._state_root / ".activation.lock"):
            current = self._read_pointer(pointer)
            if current is None or current[1] is None:
                raise AgentInstallError("managed_agent_rollback_unavailable")
            current_artifact, previous_artifact, _ = current
            activation = AgentActivationV1(
                activation_id=_activation_id(
                    previous_artifact.install_id,
                    current_artifact.install_id,
                    profile_digest,
                    compatibility_observation_digest,
                ),
                install_id=previous_artifact.install_id,
                previous_install_id=current_artifact.install_id,
                local_agent_id=local_agent_id,
                profile_digest=profile_digest,
                compatibility_observation_digest=compatibility_observation_digest,
                activated_at=activated_at,
                status=AgentActivationStatus.READY,
            )
            atomic_write_json(
                pointer,
                {
                    "schema_version": 1,
                    "current_artifact": managed_agent_artifact_to_dict(
                        previous_artifact
                    ),
                    "previous_artifact": managed_agent_artifact_to_dict(
                        current_artifact
                    ),
                    "activation": agent_activation_to_dict(activation),
                },
            )
            return activation

    def current(
        self,
        local_agent_id: str,
    ) -> tuple[ManagedAgentArtifactV1, AgentActivationV1] | None:
        """Read the exact current artifact and activation projection."""
        value = self._read_pointer(self._pointer_path(local_agent_id))
        if value is None:
            return None
        return value[0], value[2]

    def _pointer_path(self, local_agent_id: str) -> Path:
        if not local_agent_id or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in local_agent_id
        ):
            raise AgentInstallError("managed_agent_identity_invalid")
        return self._state_root / "active" / f"{local_agent_id}.json"

    @staticmethod
    def _read_pointer(
        path: Path,
    ) -> (
        tuple[ManagedAgentArtifactV1, ManagedAgentArtifactV1 | None, AgentActivationV1]
        | None
    ):
        if not path.exists():
            return None
        value = read_json(path)
        if (
            set(value)
            != {"schema_version", "current_artifact", "previous_artifact", "activation"}
            or value["schema_version"] != 1
        ):
            raise AgentInstallError("managed_agent_pointer_invalid")
        current = managed_agent_artifact_from_dict(
            cast(dict, value["current_artifact"])
        )
        previous_value = value["previous_artifact"]
        previous = (
            managed_agent_artifact_from_dict(cast(dict, previous_value))
            if previous_value is not None
            else None
        )
        activation = agent_activation_from_dict(cast(dict, value["activation"]))
        if activation.install_id != current.install_id:
            raise AgentInstallError("managed_agent_pointer_invalid")
        return current, previous, activation


def _activation_id(
    install_id: str,
    previous_install_id: str | None,
    profile_digest: str,
    compatibility_digest: str,
) -> str:
    return (
        "activation-"
        + canonical_digest(
            {
                "install_id": install_id,
                "previous_install_id": previous_install_id,
                "profile_digest": profile_digest,
                "compatibility_digest": compatibility_digest,
            }
        )[:24]
    )
