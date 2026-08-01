"""Atomic private retention of generated profile and probe evidence."""

from __future__ import annotations

from pathlib import Path

from gigaloom.contracts import compatibility_observation_to_dict
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_to_dict,
    managed_agent_artifact_to_dict,
)
from gigaloom.contracts.agent_installation_receipt_codec import (
    agent_installation_receipt_to_dict,
)
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import managed_probe_to_dict
from gigaloom.harnesses.agent_profiles.onboarding.profiles import (
    generated_profile_to_dict,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    ensure_private_directory,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


class ManagedOnboardingStore:
    """Retain one immutable content-free onboarding record per install id."""

    def __init__(self, data_root: str | Path) -> None:
        self._data_root = Path(data_root).resolve(strict=False)
        self._root = self._data_root / "agents" / "state"

    def save(self, result: ManagedAgentOnboardingResult) -> Path:
        """Atomically publish the complete generated/probed/activation record."""
        records = self._root / "onboarding"
        ensure_private_directory(records)
        target = records / f"{result.receipt.install_id}.json"
        with registry_cache_lock(self._root / ".onboarding.lock"):
            if target.exists():
                raise ValueError("managed onboarding record is immutable")
            atomic_write_json(
                target,
                {
                    "schema_version": 1,
                    "active": result.active,
                    "artifact": self._artifact_projection(result),
                    "profile": generated_profile_to_dict(result.profile),
                    "probe": managed_probe_to_dict(result.probe),
                    "compatibility": compatibility_observation_to_dict(
                        result.compatibility
                    ),
                    "activation": agent_activation_to_dict(result.activation),
                    "receipt": agent_installation_receipt_to_dict(result.receipt),
                    "content_free": True,
                },
            )
        return target

    def _artifact_projection(
        self,
        result: ManagedAgentOnboardingResult,
    ) -> dict[str, object]:
        payload = managed_agent_artifact_to_dict(result.artifact)
        managed = Path(result.artifact.managed_root).resolve(strict=False)
        try:
            relative = managed.relative_to(self._data_root)
        except ValueError as error:
            raise ValueError(
                "managed artifact path is outside the data root"
            ) from error
        payload.pop("managed_root")
        payload["managed_root_relative"] = relative.as_posix()
        return payload
