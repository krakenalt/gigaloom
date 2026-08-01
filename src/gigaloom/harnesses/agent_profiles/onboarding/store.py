"""Atomic private retention of generated profile and probe evidence."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Mapping, cast

from gigaloom.contracts import (
    compatibility_observation_from_dict,
    compatibility_observation_to_dict,
)
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_from_dict,
    agent_activation_to_dict,
    managed_agent_artifact_from_dict,
    managed_agent_artifact_to_dict,
)
from gigaloom.contracts.agent_installation_receipt_codec import (
    agent_installation_receipt_from_dict,
    agent_installation_receipt_to_dict,
)
from gigaloom.contracts.operational_validation import validate_identity
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import (
    managed_probe_from_dict,
    managed_probe_to_dict,
)
from gigaloom.harnesses.agent_profiles.onboarding.profiles import (
    generated_profile_from_dict,
    generated_profile_to_dict,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
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
                    "architecture": result.architecture,
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

    def records(self) -> tuple[ManagedAgentOnboardingResult, ...]:
        """Load every immutable onboarding record in deterministic order."""
        records = self._root / "onboarding"
        if not records.exists():
            return ()
        if records.is_symlink() or not records.is_dir():
            raise ValueError("managed onboarding records root is invalid")
        paths = tuple(sorted(records.glob("*.json"), key=lambda item: item.name))
        if len(paths) > 1_000:
            raise ValueError("managed onboarding record count exceeds its bound")
        return tuple(self._decode(read_json(path)) for path in paths)

    def get(self, install_id: str) -> ManagedAgentOnboardingResult:
        """Load one exact immutable install record."""
        validate_identity(install_id, field_name="managed install id")
        path = self._root / "onboarding" / f"{install_id}.json"
        return self._decode(read_json(path))

    def remove_record(self, install_id: str) -> None:
        """Remove one selected private state record after artifact cleanup."""
        validate_identity(install_id, field_name="managed install id")
        path = self._root / "onboarding" / f"{install_id}.json"
        with registry_cache_lock(self._root / ".onboarding.lock"):
            if path.is_symlink():
                raise ValueError("managed onboarding record cannot be a symlink")
            path.unlink(missing_ok=False)

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

    def _decode(
        self,
        value: Mapping[str, object],
    ) -> ManagedAgentOnboardingResult:
        expected = {
            "schema_version",
            "active",
            "architecture",
            "artifact",
            "profile",
            "probe",
            "compatibility",
            "activation",
            "receipt",
            "content_free",
        }
        if (
            set(value) != expected
            or value["schema_version"] != 1
            or value["content_free"] is not True
            or not isinstance(value["active"], bool)
        ):
            raise ValueError("managed onboarding record fields are invalid")
        artifact_payload = dict(_mapping(value["artifact"], "artifact"))
        relative_value = artifact_payload.pop("managed_root_relative", None)
        if not isinstance(relative_value, str):
            raise ValueError("managed artifact relative root is invalid")
        relative = PurePosixPath(relative_value)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("managed artifact relative root is unsafe")
        artifact_payload["managed_root"] = str(
            self._data_root.joinpath(*relative.parts)
        )
        return ManagedAgentOnboardingResult(
            artifact=managed_agent_artifact_from_dict(artifact_payload),
            architecture=_string(value["architecture"], "architecture"),
            profile=generated_profile_from_dict(_mapping(value["profile"], "profile")),
            probe=managed_probe_from_dict(_mapping(value["probe"], "probe")),
            compatibility=compatibility_observation_from_dict(
                _mapping(value["compatibility"], "compatibility")
            ),
            activation=agent_activation_from_dict(
                _mapping(value["activation"], "activation")
            ),
            receipt=agent_installation_receipt_from_dict(
                _mapping(value["receipt"], "receipt")
            ),
            active=cast(bool, value["active"]),
        )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"managed onboarding {label} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"managed onboarding {label} must be text")
    return value
