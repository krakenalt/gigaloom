"""Fresh probe evidence and atomic activation for retained managed ACP artifacts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Mapping, cast

from gigaloom.contracts import (
    InstallationTransitionV1,
    compatibility_observation_from_dict,
    compatibility_observation_to_dict,
)
from gigaloom.contracts.agent_installation_codec import (
    agent_activation_from_dict,
    agent_activation_to_dict,
)
from gigaloom.contracts.agent_installation_receipt_codec import (
    agent_installation_receipt_from_dict,
    agent_installation_receipt_to_dict,
)
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.activation import (
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.installations.filesystem import (
    atomic_write_json,
    read_json,
)
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import (
    managed_probe_from_dict,
    managed_probe_to_dict,
)
from gigaloom.harnesses.agent_profiles.onboarding.service import (
    DEFAULT_COMPATIBILITY_TTL,
    build_compatibility_observation,
    build_inactive_activation,
    managed_activation_omissions,
    managed_activation_status,
    managed_probe_is_activatable,
)
from gigaloom.harnesses.agent_profiles.registry.locking import registry_cache_lock


_ACTIVATION_ID_RE = re.compile(r"activation-[0-9a-f]{24}\Z")


class ManagedAgentReactivationService:
    """Re-probe one exact retained artifact before publishing its active pointer."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        clock,
        compatibility_ttl: timedelta = DEFAULT_COMPATIBILITY_TTL,
    ) -> None:  # noqa: ANN001
        self._clock = clock
        self._activations = ManagedAgentActivationStore(data_root)
        self._evidence = ManagedReactivationStore(data_root)
        self._compatibility_ttl = compatibility_ttl
        if not timedelta(minutes=1) <= compatibility_ttl <= timedelta(days=30):
            raise ValueError("managed compatibility TTL is outside the supported bound")

    def activate(
        self,
        record: ManagedAgentOnboardingResult,
        probe: ManagedAcpProbeReceipt,
    ) -> ManagedAgentOnboardingResult:
        """Persist fresh content-free evidence before an atomic active pointer."""
        now = self._now(record)
        compatibility = build_compatibility_observation(
            record.profile,
            record.artifact,
            probe,
            reviewed_version=record.artifact.version,
            entry_digest=record.receipt.entry_digest,
            observed_at=now,
            expires_at=now + self._compatibility_ttl,
        )
        if not managed_probe_is_activatable(compatibility, probe):
            current = self._activations.current(record.artifact.local_agent_id)
            activation = build_inactive_activation(
                record.artifact,
                record.profile.profile_digest,
                compatibility.probe_digest,
                now,
                previous_install_id=(current[0].install_id if current else None),
            )
            result = _reactivation_result(
                record,
                probe,
                compatibility,
                activation,
                active=False,
                finished_at=now,
            )
            self._evidence.save(result)
            return result

        published: list[ManagedAgentOnboardingResult] = []

        def publish_evidence(activation) -> None:  # noqa: ANN001
            result = _reactivation_result(
                record,
                probe,
                compatibility,
                activation,
                active=True,
                finished_at=now,
            )
            self._evidence.save(result)
            published.append(result)

        self._activations.activate(
            record.artifact,
            profile_digest=record.profile.profile_digest,
            compatibility_observation_digest=compatibility.probe_digest,
            status=managed_activation_status(probe),
            activated_at=now,
            publish_evidence=publish_evidence,
        )
        if len(published) != 1:
            raise RuntimeError("managed activation evidence was not published")
        return published[0]

    def project(
        self,
        record: ManagedAgentOnboardingResult,
        *,
        activation_id: str | None,
    ) -> ManagedAgentOnboardingResult:
        """Overlay the exact active or latest retained reactivation evidence."""
        result = (
            self._evidence.get(record, activation_id)
            if activation_id is not None
            else self._evidence.latest(record)
        )
        return result or record

    def project_many(
        self,
        records: tuple[ManagedAgentOnboardingResult, ...],
        *,
        active_activation_ids: Mapping[str, str],
    ) -> tuple[ManagedAgentOnboardingResult, ...]:
        """Overlay bounded reactivation evidence with one state-directory scan."""
        projected = self._evidence.select(records, active_activation_ids)
        return tuple(projected.get(item.artifact.install_id, item) for item in records)

    def _now(self, record: ManagedAgentOnboardingResult) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("managed reactivation clock must be timezone-aware")
        if value < record.receipt.finished_at:
            raise ValueError("managed reactivation predates installation evidence")
        return value


class ManagedReactivationStore:
    """Retain immutable content-free evidence for each activation attempt."""

    def __init__(self, data_root: str | Path) -> None:
        self._root = Path(data_root).expanduser().resolve(strict=False) / (
            "agents/state/reactivations"
        )

    def save(self, result: ManagedAgentOnboardingResult) -> Path:
        if _ACTIVATION_ID_RE.fullmatch(result.activation.activation_id) is None:
            raise ValueError("managed reactivation activation id is invalid")
        payload = _encode(result)
        target = self._root / f"{result.activation.activation_id}.json"
        with registry_cache_lock(self._root.parent / ".reactivation.lock"):
            if target.exists():
                if read_json(target) != payload:
                    raise ValueError("managed reactivation evidence is immutable")
                return target
            atomic_write_json(target, payload)
        return target

    def get(
        self,
        base: ManagedAgentOnboardingResult,
        activation_id: str,
    ) -> ManagedAgentOnboardingResult | None:
        if _ACTIVATION_ID_RE.fullmatch(activation_id) is None:
            raise ValueError("managed reactivation activation id is invalid")
        target = self._root / f"{activation_id}.json"
        if not target.exists():
            return None
        return _decode(base, read_json(target))

    def latest(
        self,
        base: ManagedAgentOnboardingResult,
    ) -> ManagedAgentOnboardingResult | None:
        if not self._root.exists():
            return None
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("managed reactivation root is invalid")
        paths = tuple(sorted(self._root.glob("activation-*.json")))
        if len(paths) > 1_000:
            raise ValueError("managed reactivation evidence exceeds its bound")
        matches = []
        for path in paths:
            value = read_json(path)
            if _base_install_id(value) == base.artifact.install_id:
                result = _decode(base, value)
                if not result.active:
                    matches.append(result)
        return max(matches, key=lambda item: item.activation.activated_at, default=None)

    def select(
        self,
        bases: tuple[ManagedAgentOnboardingResult, ...],
        active_activation_ids: Mapping[str, str],
    ) -> dict[str, ManagedAgentOnboardingResult]:
        """Select exact active or latest inactive evidence in one bounded scan."""
        if not self._root.exists():
            return {}
        if self._root.is_symlink() or not self._root.is_dir():
            raise ValueError("managed reactivation root is invalid")
        paths = tuple(sorted(self._root.glob("activation-*.json")))
        if len(paths) > 1_000:
            raise ValueError("managed reactivation evidence exceeds its bound")
        base_by_install = {item.artifact.install_id: item for item in bases}
        selected: dict[str, ManagedAgentOnboardingResult] = {}
        for path in paths:
            value = read_json(path)
            install_id = _base_install_id(value)
            base = base_by_install.get(install_id)
            if base is None:
                continue
            result = _decode(base, value)
            active_id = active_activation_ids.get(install_id)
            if active_id is not None:
                if result.activation.activation_id == active_id:
                    selected[install_id] = result
                continue
            if result.active:
                continue
            previous = selected.get(install_id)
            if (
                previous is None
                or result.activation.activated_at > previous.activation.activated_at
            ):
                selected[install_id] = result
        return selected


def _reactivation_result(
    base: ManagedAgentOnboardingResult,
    probe,
    compatibility,
    activation,
    *,
    active: bool,
    finished_at: datetime,
) -> ManagedAgentOnboardingResult:  # noqa: ANN001
    if len(base.receipt.transitions) > 254:
        raise ValueError("managed reactivation transition history is exhausted")
    transitions = (
        *base.receipt.transitions,
        _transition(
            len(base.receipt.transitions),
            "reactivation_probe_completed",
            "managed_reactivation_probe_completed",
            probe.receipt_digest,
            finished_at,
        ),
        _transition(
            len(base.receipt.transitions) + 1,
            f"activation_{activation.status.value}",
            "managed_reactivation_completed",
            activation.activation_id,
            finished_at,
        ),
    )
    receipt = replace(
        base.receipt,
        receipt_id="receipt-"
        + canonical_digest(
            {
                "base_receipt_id": base.receipt.receipt_id,
                "probe_digest": compatibility.probe_digest,
                "activation_id": activation.activation_id,
            }
        )[:24],
        transitions=transitions,
        probe_observation_digest=compatibility.probe_digest,
        activation_id=activation.activation_id,
        rollback_install_id=activation.previous_install_id,
        omissions=managed_activation_omissions(probe, active=active),
        finished_at=finished_at,
    )
    return ManagedAgentOnboardingResult(
        artifact=base.artifact,
        architecture=base.architecture,
        profile=base.profile,
        probe=probe,
        compatibility=compatibility,
        activation=activation,
        receipt=receipt,
        active=active,
    )


def _transition(
    sequence: int,
    state: str,
    reason_code: str,
    evidence: str,
    timestamp: datetime,
) -> InstallationTransitionV1:
    return InstallationTransitionV1(
        sequence=sequence,
        state=state,
        timestamp=timestamp,
        evidence_digest=canonical_digest({"evidence": evidence}),
        reason_code=reason_code,
    )


def _encode(result: ManagedAgentOnboardingResult) -> dict[str, object]:
    return {
        "schema_version": 1,
        "base_install_id": result.artifact.install_id,
        "active": result.active,
        "probe": managed_probe_to_dict(result.probe),
        "compatibility": compatibility_observation_to_dict(result.compatibility),
        "activation": agent_activation_to_dict(result.activation),
        "receipt": agent_installation_receipt_to_dict(result.receipt),
        "content_free": True,
    }


def _decode(
    base: ManagedAgentOnboardingResult,
    value: Mapping[str, object],
) -> ManagedAgentOnboardingResult:
    expected = {
        "schema_version",
        "base_install_id",
        "active",
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
        or _base_install_id(value) != base.artifact.install_id
        or not isinstance(value["active"], bool)
    ):
        raise ValueError("managed reactivation evidence is invalid")
    return ManagedAgentOnboardingResult(
        artifact=base.artifact,
        architecture=base.architecture,
        profile=base.profile,
        probe=managed_probe_from_dict(_mapping(value["probe"])),
        compatibility=compatibility_observation_from_dict(
            _mapping(value["compatibility"])
        ),
        activation=agent_activation_from_dict(_mapping(value["activation"])),
        receipt=agent_installation_receipt_from_dict(_mapping(value["receipt"])),
        active=cast(bool, value["active"]),
    )


def _base_install_id(value: Mapping[str, object]) -> str:
    install_id = value.get("base_install_id")
    if not isinstance(install_id, str):
        raise ValueError("managed reactivation base install id is invalid")
    return install_id


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("managed reactivation field must be an object")
    return cast(Mapping[str, object], value)
