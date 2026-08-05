"""Immutable content-free managed ACP onboarding projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from gigaloom.contracts import (
    AgentActivationV1,
    AgentInstallationReceiptV1,
    CompatibilityObservationV1,
    ManagedAgentArtifactV1,
)
from gigaloom.contracts.operational_validation import (
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_optional_digest,
    validate_text,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1


class ManagedProbeState(str, Enum):
    """Terminal initialize-only probe outcome."""

    READY = "ready"
    AUTH_REQUIRED = "auth_required"
    DEGRADED = "degraded"
    INCOMPATIBLE = "incompatible"
    UNSAFE = "unsafe"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ManagedAcpProviderBridgeProjection:
    """Persisted content-free provider bridge capability for one probe."""

    status: str
    strategy: str | None
    protocols: tuple[str, ...]
    provider_ids: tuple[str, ...]
    adapter_id: str | None
    adapter_revision: str | None
    model_selection: str | None
    reason_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.status not in {
            "ready",
            "native_only",
            "blocked",
            "unknown_until_reprobe",
        }:
            raise ValueError("managed ACP provider bridge status is invalid")
        if self.strategy not in {
            None,
            "acp_providers",
            "openai_env",
            "ephemeral_config",
        }:
            raise ValueError("managed ACP provider bridge strategy is invalid")
        for field_name in ("protocols", "provider_ids", "reason_ids"):
            object.__setattr__(
                self,
                field_name,
                normalize_identities(
                    getattr(self, field_name),
                    field_name=f"managed ACP provider bridge {field_name}",
                ),
            )
        for value, field_name in (
            (self.adapter_id, "managed ACP provider bridge adapter id"),
            (self.adapter_revision, "managed ACP provider bridge adapter revision"),
            (self.model_selection, "managed ACP provider bridge model selection"),
        ):
            if value is not None:
                validate_identity(value, field_name=field_name)
        if self.status == "ready" and self.strategy is None:
            raise ValueError("ready managed ACP provider bridge requires a strategy")
        if self.status != "ready" and self.strategy is not None:
            raise ValueError(
                "unready managed ACP provider bridge cannot select a strategy"
            )

    def projection(self) -> dict[str, object]:
        """Return the persisted diagnostic shape without runtime secrets."""
        return {
            "status": self.status,
            "strategy": self.strategy,
            "protocols": list(self.protocols),
            "provider_ids": list(self.provider_ids),
            "adapter_id": self.adapter_id,
            "adapter_revision": self.adapter_revision,
            "model_selection": self.model_selection,
            "reason_ids": list(self.reason_ids),
        }


def unknown_provider_bridge_projection() -> ManagedAcpProviderBridgeProjection:
    """Return the additive migration state for a record without bridge facts."""
    return ManagedAcpProviderBridgeProjection(
        status="unknown_until_reprobe",
        strategy=None,
        protocols=(),
        provider_ids=(),
        adapter_id=None,
        adapter_revision=None,
        model_selection=None,
        reason_ids=("provider_bridge_reprobe_required",),
    )


@dataclass(frozen=True, slots=True)
class ManagedAcpProbeReceipt:
    """Content-free initialize projection; no session or prompt can be claimed."""

    state: ManagedProbeState
    protocol_state: str
    protocol_version: str | None
    capability_snapshot_digest: str | None
    process_fingerprint: str
    executable_observed: bool
    handshake_digest: str
    auth_methods: tuple[str, ...]
    capabilities: tuple[str, ...]
    losses: tuple[str, ...]
    warnings: tuple[str, ...]
    native_home_isolated: bool
    network_policy: str
    receipt_digest: str
    provider_bridge: ManagedAcpProviderBridgeProjection = field(
        default_factory=unknown_provider_bridge_projection
    )
    session_created: bool = False
    prompt_sent: bool = False
    content_free: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.state, ManagedProbeState):
            raise ValueError("managed ACP probe state is invalid")
        validate_text(
            self.protocol_state,
            field_name="managed ACP protocol state",
            max_chars=64,
        )
        if self.protocol_version is not None:
            validate_text(
                self.protocol_version,
                field_name="managed ACP protocol version",
                max_chars=64,
            )
        validate_optional_digest(
            self.capability_snapshot_digest,
            field_name="managed ACP capability snapshot digest",
        )
        for value, label in (
            (self.process_fingerprint, "managed ACP process fingerprint"),
            (self.handshake_digest, "managed ACP handshake digest"),
            (self.receipt_digest, "managed ACP probe receipt digest"),
        ):
            validate_digest(value, field_name=label)
        if not isinstance(self.executable_observed, bool):
            raise ValueError("managed ACP executable observation flag is invalid")
        if not isinstance(
            self.provider_bridge,
            ManagedAcpProviderBridgeProjection,
        ):
            raise ValueError("managed ACP provider bridge projection is invalid")
        for field_name in ("auth_methods", "capabilities", "losses", "warnings"):
            object.__setattr__(
                self,
                field_name,
                normalize_identities(
                    getattr(self, field_name),
                    field_name=f"managed ACP probe {field_name}",
                ),
            )
        validate_text(
            self.network_policy,
            field_name="managed ACP network policy",
            max_chars=64,
        )
        if self.native_home_isolated is not True:
            raise ValueError("managed ACP probe requires isolated native home")
        if self.session_created or self.prompt_sent:
            raise ValueError("managed ACP probe cannot create a session or prompt")
        if self.content_free is not True:
            raise ValueError("managed ACP probe receipt must be content-free")


@dataclass(frozen=True, slots=True)
class ManagedAgentOnboardingResult:
    """Generated route, probe evidence, activation, and terminal receipt."""

    artifact: ManagedAgentArtifactV1
    architecture: str
    profile: AgentProfileV1
    probe: ManagedAcpProbeReceipt
    compatibility: CompatibilityObservationV1
    activation: AgentActivationV1
    receipt: AgentInstallationReceiptV1
    active: bool

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, ManagedAgentArtifactV1):
            raise ValueError("managed onboarding artifact is invalid")
        validate_identity(
            self.architecture,
            field_name="managed onboarding architecture",
        )
        if not isinstance(self.profile, AgentProfileV1):
            raise ValueError("managed onboarding profile is invalid")
        for value, expected, label in (
            (self.probe, ManagedAcpProbeReceipt, "probe"),
            (self.compatibility, CompatibilityObservationV1, "compatibility"),
            (self.activation, AgentActivationV1, "activation"),
            (self.receipt, AgentInstallationReceiptV1, "installation receipt"),
        ):
            if not isinstance(value, expected):
                raise ValueError(f"managed onboarding {label} is invalid")
        if not isinstance(self.active, bool):
            raise ValueError("managed onboarding active flag is invalid")
        if self.receipt.probe_observation_digest != self.compatibility.probe_digest:
            raise ValueError(
                "onboarding receipt is not bound to compatibility evidence"
            )
        if self.receipt.activation_id != self.activation.activation_id:
            raise ValueError("onboarding receipt is not bound to activation evidence")
