"""One generic install-to-profile-to-probe-to-activation transaction."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from gigaloom.contracts import (
    ACPRegistryEntryV1,
    AgentActivationStatus,
    AgentActivationV1,
    AgentCleanupStatus,
    AgentInstallationOutcome,
    AgentInstallationReceiptV1,
    AgentInstallPlanV1,
    CapabilityAdmissionV1,
    CompatibilityProbeCacheKeyV1,
    CompatibilityStatus,
    ExecutableObservationV1,
    ExtractionLimitsV1,
    InstallationTransitionV1,
    ManagedAgentArtifactV1,
    ProtocolNegotiationState,
    ProtocolNegotiationV1,
    ReviewedVersionEvidenceV1,
    ReviewedVersionState,
    SecurityCompatibilityV1,
    evaluate_compatibility,
)
from gigaloom.contracts.compatibility_fingerprints import digest_command_tokens
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.agent_profiles.installations.activation import (
    ManagedAgentActivationStore,
)
from gigaloom.harnesses.agent_profiles.installations.binary import (
    DEFAULT_BINARY_EXTRACTION_LIMITS,
)
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedAgentOnboardingResult,
)
from gigaloom.harnesses.agent_profiles.onboarding.probe import ManagedAcpProbePort
from gigaloom.harnesses.agent_profiles.onboarding.profiles import (
    generate_managed_agent_profile,
)
from gigaloom.harnesses.agent_profiles.onboarding.store import (
    ManagedOnboardingStore,
)


DEFAULT_COMPATIBILITY_TTL = timedelta(hours=24)


class ManagedAgentOnboardingService:
    """Generate, initialize-probe, retain, and conditionally activate a candidate."""

    def __init__(
        self,
        data_root: str,
        probe: ManagedAcpProbePort,
        *,
        clock: Callable[[], datetime] | None = None,
        compatibility_ttl: timedelta = DEFAULT_COMPATIBILITY_TTL,
    ) -> None:
        self._probe = probe
        self._clock = clock or (lambda: datetime.now(UTC))
        self._activation_store = ManagedAgentActivationStore(data_root)
        self._record_store = ManagedOnboardingStore(data_root)
        self._compatibility_ttl = compatibility_ttl
        if not timedelta(minutes=1) <= compatibility_ttl <= timedelta(days=30):
            raise ValueError("managed compatibility TTL is outside the supported bound")

    def onboard(
        self,
        plan: AgentInstallPlanV1,
        entry: ACPRegistryEntryV1,
        artifact: ManagedAgentArtifactV1,
        *,
        network_isolated: bool,
        transitions: tuple[InstallationTransitionV1, ...] = (),
        bytes_received: int = 0,
        extraction_limits: ExtractionLimitsV1 = DEFAULT_BINARY_EXTRACTION_LIMITS,
    ) -> ManagedAgentOnboardingResult:
        """Complete onboarding while retaining incompatible installs as inactive."""
        self._validate_bindings(plan, entry, artifact, transitions, bytes_received)
        now = self._now()
        evidence = _ensure_initial_transition(
            transitions,
            artifact,
            timestamp=now,
        )
        profile = generate_managed_agent_profile(entry, artifact)
        evidence = _append_transition(
            evidence,
            "profile_generated",
            "managed_profile_generated",
            profile.profile_digest,
            now,
        )
        probe = self._probe.probe(
            profile,
            artifact,
            network_isolated=network_isolated,
        )
        evidence = _append_transition(
            evidence,
            "probe_completed",
            f"managed_probe_{probe.state.value}",
            probe.receipt_digest,
            now,
        )
        compatibility = build_compatibility_observation(
            profile,
            artifact,
            probe,
            reviewed_version=entry.version,
            entry_digest=entry.entry_digest,
            observed_at=now,
            expires_at=now + self._compatibility_ttl,
        )
        active = managed_probe_is_activatable(compatibility, probe)
        if active:
            activation = self._activation_store.activate(
                artifact,
                profile_digest=profile.profile_digest,
                compatibility_observation_digest=compatibility.probe_digest,
                status=managed_activation_status(probe),
                activated_at=now,
            )
        else:
            current = self._activation_store.current(artifact.local_agent_id)
            activation = build_inactive_activation(
                artifact,
                profile.profile_digest,
                compatibility.probe_digest,
                now,
                previous_install_id=(current[0].install_id if current else None),
            )
        evidence = _append_transition(
            evidence,
            f"activation_{activation.status.value}",
            "managed_activation_completed",
            activation.activation_id,
            now,
        )
        omissions = managed_activation_omissions(probe, active=active)
        receipt = AgentInstallationReceiptV1(
            receipt_id="receipt-"
            + canonical_digest(
                {
                    "plan_id": plan.plan_id,
                    "install_id": artifact.install_id,
                    "profile_digest": profile.profile_digest,
                    "probe_digest": compatibility.probe_digest,
                    "activation_id": activation.activation_id,
                }
            )[:24],
            plan_id=plan.plan_id,
            install_id=artifact.install_id,
            registry_id=entry.registry_id,
            entry_digest=entry.entry_digest,
            snapshot_digest=entry.snapshot_digest,
            transitions=evidence,
            bytes_received=bytes_received,
            artifact_digest=artifact.artifact_digest,
            package_integrity=artifact.package_integrity,
            extraction_limits=extraction_limits,
            probe_observation_digest=compatibility.probe_digest,
            activation_id=activation.activation_id,
            rollback_install_id=activation.previous_install_id,
            omissions=omissions,
            cleanup_status=AgentCleanupStatus.NOT_REQUIRED,
            outcome=AgentInstallationOutcome.SUCCEEDED,
            started_at=evidence[0].timestamp,
            finished_at=now,
        )
        result = ManagedAgentOnboardingResult(
            artifact=artifact,
            architecture=plan.architecture,
            profile=profile,
            probe=probe,
            compatibility=compatibility,
            activation=activation,
            receipt=receipt,
            active=active,
        )
        self._record_store.save(result)
        return result

    @staticmethod
    def _validate_bindings(
        plan: AgentInstallPlanV1,
        entry: ACPRegistryEntryV1,
        artifact: ManagedAgentArtifactV1,
        transitions: tuple[InstallationTransitionV1, ...],
        bytes_received: int,
    ) -> None:
        if (
            plan.registry_id != entry.registry_id
            or plan.entry_digest != entry.entry_digest
            or plan.snapshot_digest != entry.snapshot_digest
            or plan.local_agent_id != artifact.local_agent_id
            or plan.registry_id != artifact.registry_id
            or plan.version != artifact.version
            or plan.distribution_kind is not artifact.distribution_kind
        ):
            raise ValueError("managed onboarding evidence is not bound")
        if (
            not isinstance(transitions, tuple)
            or any(
                not isinstance(item, InstallationTransitionV1) for item in transitions
            )
            or tuple(item.sequence for item in transitions)
            != tuple(range(len(transitions)))
        ):
            raise ValueError("managed onboarding transitions are invalid")
        if (
            isinstance(bytes_received, bool)
            or not isinstance(bytes_received, int)
            or bytes_received < 0
        ):
            raise ValueError("managed onboarding bytes_received is invalid")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("managed onboarding clock must be timezone-aware")
        return value


def build_compatibility_observation(
    profile,
    artifact,
    probe: ManagedAcpProbeReceipt,
    *,
    reviewed_version: str,
    entry_digest: str,
    observed_at: datetime,
    expires_at: datetime,
):  # noqa: ANN001, ANN202
    route = profile.structured_routes[0]
    required = route.capability_requirements
    missing = tuple(sorted(set(required) - set(probe.capabilities)))
    protocol_state = ProtocolNegotiationState(probe.protocol_state)
    cache_key = CompatibilityProbeCacheKeyV1(
        executable_identity=probe.process_fingerprint,
        profile_digest=profile.profile_digest,
        command_tokens_digest=digest_command_tokens(
            (
                artifact.executable_relative_path,
                *artifact.arguments,
            )
        ),
        protocol_handshake_digest=probe.handshake_digest,
        platform=artifact.platform,
    )
    invariant_failures = (
        probe.warnings if protocol_state is ProtocolNegotiationState.MALFORMED else ()
    )
    return evaluate_compatibility(
        agent_id=profile.agent_id,
        route_id=route.route_id,
        profile_digest=profile.profile_digest,
        executable=ExecutableObservationV1(
            executable_identity=probe.process_fingerprint,
            reported_version=reviewed_version if probe.executable_observed else None,
            observed=probe.executable_observed,
        ),
        reviewed_version=ReviewedVersionEvidenceV1(
            state=ReviewedVersionState.NOT_APPLICABLE,
            evidence_digest=entry_digest,
            exact_evidence_matched=False,
        ),
        protocol=ProtocolNegotiationV1(
            protocol_family="acp",
            protocol_version=probe.protocol_version,
            state=protocol_state,
            handshake_digest=probe.handshake_digest,
        ),
        capabilities=CapabilityAdmissionV1(
            capability_fingerprint=canonical_digest(
                {
                    "capabilities": list(probe.capabilities),
                    "losses": list(probe.losses),
                    "auth_methods": list(probe.auth_methods),
                }
            ),
            required_capabilities=required,
            missing_capabilities=missing,
        ),
        security=SecurityCompatibilityV1(
            invariant_failures=invariant_failures,
        ),
        cache_key=cache_key,
        observed_at=observed_at,
        expires_at=expires_at,
    )


def _ensure_initial_transition(
    transitions: tuple[InstallationTransitionV1, ...],
    artifact: ManagedAgentArtifactV1,
    *,
    timestamp: datetime,
) -> tuple[InstallationTransitionV1, ...]:
    if transitions:
        if transitions[-1].timestamp > timestamp:
            raise ValueError("managed onboarding transitions are from the future")
        return transitions
    return (
        InstallationTransitionV1(
            sequence=0,
            state="installed_inactive",
            timestamp=timestamp,
            evidence_digest=artifact.artifact_digest,
            reason_code="managed_artifact_installed",
        ),
    )


def _append_transition(
    transitions: tuple[InstallationTransitionV1, ...],
    state: str,
    reason_code: str,
    evidence: str,
    timestamp: datetime,
) -> tuple[InstallationTransitionV1, ...]:
    return (
        *transitions,
        InstallationTransitionV1(
            sequence=len(transitions),
            state=state,
            timestamp=timestamp,
            evidence_digest=canonical_digest({"evidence": evidence}),
            reason_code=reason_code,
        ),
    )


def managed_probe_is_activatable(
    compatibility,
    probe: ManagedAcpProbeReceipt,
) -> bool:  # noqa: ANN001
    return (
        compatibility.status
        in {
            CompatibilityStatus.VERIFIED,
            CompatibilityStatus.COMPATIBLE_UNVERIFIED,
        }
        and probe.protocol_state == ProtocolNegotiationState.CONFORMANT.value
    )


def managed_activation_status(
    probe: ManagedAcpProbeReceipt,
) -> AgentActivationStatus:
    return (
        AgentActivationStatus.DEGRADED
        if probe.auth_methods or probe.losses or probe.warnings
        else AgentActivationStatus.READY
    )


def build_inactive_activation(
    artifact: ManagedAgentArtifactV1,
    profile_digest: str,
    compatibility_digest: str,
    activated_at: datetime,
    *,
    previous_install_id: str | None,
) -> AgentActivationV1:
    activation_id = (
        "activation-"
        + canonical_digest(
            {
                "install_id": artifact.install_id,
                "previous_install_id": previous_install_id,
                "profile_digest": profile_digest,
                "compatibility_digest": compatibility_digest,
                "status": AgentActivationStatus.INACTIVE.value,
                "activated_at": activated_at.isoformat(),
            }
        )[:24]
    )
    return AgentActivationV1(
        activation_id=activation_id,
        install_id=artifact.install_id,
        previous_install_id=previous_install_id,
        local_agent_id=artifact.local_agent_id,
        profile_digest=profile_digest,
        compatibility_observation_digest=compatibility_digest,
        activated_at=activated_at,
        status=AgentActivationStatus.INACTIVE,
    )


def managed_activation_omissions(
    probe: ManagedAcpProbeReceipt,
    *,
    active: bool,
) -> tuple[str, ...]:
    values = set()
    if probe.auth_methods:
        values.add("authentication_not_completed")
    if probe.losses:
        values.add("optional_capability_losses")
    if not active:
        values.add("activation_withheld_incompatible")
    return tuple(sorted(values))
