"""Fail-closed agent compatibility admission for reviewed gateway launches."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from importlib import metadata
import re
from typing import Any

from gigaloom.harnesses.acp.api import ACP_PROTOCOL_VERSION
from gigaloom.registry import (
    HarnessRegistry,
    UnknownHarnessError,
    create_default_registry,
)


GATEWAY_COMPATIBILITY_SCHEMA_VERSION = 1
ACP_SDK_DISTRIBUTION = "agent-client-protocol"
ACP_SDK_VERSION = "0.11.1"
ACP_WIRE_PROTOCOL_VERSION = str(ACP_PROTOCOL_VERSION)
_ACP_EXPECTED = (
    f"{ACP_SDK_DISTRIBUTION}=={ACP_SDK_VERSION};protocol=={ACP_WIRE_PROTOCOL_VERSION}"
)
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")


@dataclass(frozen=True, slots=True)
class GatewayAgentCompatibilityContractV1:
    """One exact reviewed agent window for gateway overlay execution."""

    agent_id: str
    harness_id: str
    minimum_version: str
    maximum_version_exclusive: str
    required_capabilities: tuple[str, ...]
    pinned_version: str | None = None

    @property
    def version_window(self) -> str:
        """Return the compact public version-window notation."""
        if self.pinned_version is not None:
            return f"=={self.pinned_version}"
        return f">={self.minimum_version},<{self.maximum_version_exclusive}"


@dataclass(frozen=True, slots=True)
class GatewayAgentCompatibilityDecisionV1:
    """Content-free admission result before sidecar or agent execution."""

    agent_id: str
    harness_id: str
    status: str
    reason_id: str
    expected_version_window: str
    observed_version: str | None = None
    missing_capabilities: tuple[str, ...] = ()
    fallback_allowed: bool = False

    @property
    def ready(self) -> bool:
        """Return whether exact gateway-agent evidence was admitted."""
        return self.status == "ready"


GatewayAgentCompatibilityResolver = Callable[[str], GatewayAgentCompatibilityDecisionV1]
PackageVersionResolver = Callable[[str], str]


GATEWAY_AGENT_COMPATIBILITY_CONTRACTS: Mapping[
    str, GatewayAgentCompatibilityContractV1
] = {
    contract.agent_id: contract
    for contract in (
        GatewayAgentCompatibilityContractV1(
            "codex",
            "codex-cli",
            "0.146.0",
            "0.146.1",
            ("--json", "--sandbox", "--ephemeral", "app-server"),
            "0.146.0",
        ),
        GatewayAgentCompatibilityContractV1(
            "claude",
            "claude-code",
            "2.1.0",
            "2.2.0",
            (
                "--output-format",
                "stream-json",
                "--permission-mode",
                "--no-session-persistence",
            ),
        ),
        GatewayAgentCompatibilityContractV1(
            "gemini",
            "gemini-cli",
            "0.46.0",
            "0.47.0",
            ("--output-format", "stream-json", "--approval-mode", "--skip-trust"),
        ),
    )
}


def evaluate_gateway_cli_compatibility(
    agent_id: str,
    snapshot: object,
) -> GatewayAgentCompatibilityDecisionV1:
    """Admit one CLI only from complete, internally consistent probe evidence."""
    contract = GATEWAY_AGENT_COMPATIBILITY_CONTRACTS.get(agent_id)
    if contract is None:
        return _missing_contract(agent_id)

    reject = partial(
        _blocked,
        agent_id,
        contract.harness_id,
        contract.version_window,
    )
    status = getattr(snapshot, "status", None)
    version = getattr(snapshot, "version", None)
    parsed_version = getattr(snapshot, "parsed_version", None)
    capabilities = getattr(snapshot, "capabilities", None)
    if status not in {"supported", "degraded"}:
        reason = {
            "missing": "gateway_agent_executable_missing",
            "error": "gateway_agent_capability_probe_failed",
        }.get(status, "gateway_agent_capability_probe_rejected")
        return reject(reason)
    if not isinstance(version, str) or not version.strip():
        return reject("gateway_agent_version_evidence_missing")
    observed = _parsed_version(version)
    if (
        observed is None
        or not isinstance(parsed_version, str)
        or parsed_version != observed
    ):
        return reject("gateway_agent_version_evidence_malformed", version[:200])
    if not _version_admitted(observed, contract):
        return reject("gateway_agent_version_outside_reviewed_window", observed)
    if (
        not isinstance(capabilities, Mapping)
        or len(capabilities) > 64
        or any(
            not isinstance(name, str) or not isinstance(value, bool)
            for name, value in capabilities.items()
        )
    ):
        return reject("gateway_agent_capability_evidence_malformed", observed)
    missing = tuple(
        item
        for item in contract.required_capabilities
        if capabilities.get(item) is not True
    )
    if missing:
        return reject("gateway_agent_required_capability_missing", observed, missing)
    return GatewayAgentCompatibilityDecisionV1(
        agent_id,
        contract.harness_id,
        "ready",
        "gateway_agent_compatibility_admitted",
        contract.version_window,
        observed,
    )


def evaluate_gateway_acp_compatibility(
    *,
    sdk_version: object,
    protocol_version: object,
) -> GatewayAgentCompatibilityDecisionV1:
    """Admit the managed ACP route only for the exact SDK and wire version."""

    reject = partial(
        _blocked,
        "managed-acp-agent",
        "acp",
        _ACP_EXPECTED,
    )
    if not isinstance(sdk_version, str) or not isinstance(protocol_version, str):
        return reject("gateway_acp_compatibility_evidence_malformed")
    if sdk_version != ACP_SDK_VERSION:
        return reject("gateway_acp_sdk_version_mismatch", sdk_version[:200])
    if protocol_version != ACP_WIRE_PROTOCOL_VERSION:
        return reject(
            "gateway_acp_protocol_version_mismatch",
            f"sdk={sdk_version};protocol={protocol_version}"[:200],
        )
    return GatewayAgentCompatibilityDecisionV1(
        "managed-acp-agent",
        "acp",
        "ready",
        "gateway_agent_compatibility_admitted",
        _ACP_EXPECTED,
        f"sdk={sdk_version};protocol={protocol_version}",
    )


def build_gateway_agent_compatibility_resolver(
    *,
    registry: HarnessRegistry | None = None,
    package_version: PackageVersionResolver = metadata.version,
) -> GatewayAgentCompatibilityResolver:
    """Compose bounded installed-agent probes without loading entry-point plugins."""
    harnesses = registry

    def resolve(agent_id: str) -> GatewayAgentCompatibilityDecisionV1:
        nonlocal harnesses
        if agent_id == "managed-acp-agent":
            try:
                sdk_version = package_version(ACP_SDK_DISTRIBUTION)
            except metadata.PackageNotFoundError:
                reason = "gateway_acp_sdk_missing"
            except Exception:
                reason = "gateway_acp_sdk_version_probe_failed"
            else:
                return evaluate_gateway_acp_compatibility(
                    sdk_version=sdk_version,
                    protocol_version=ACP_WIRE_PROTOCOL_VERSION,
                )
            return _blocked(agent_id, "acp", _ACP_EXPECTED, reason)
        contract = GATEWAY_AGENT_COMPATIBILITY_CONTRACTS.get(agent_id)
        if contract is None:
            return _missing_contract(agent_id)
        if harnesses is None:
            harnesses = create_default_registry(include_entry_points=False)
        try:
            harness = harnesses.get(contract.harness_id)
            probe = getattr(harness, "capability_probe", None)
            if not callable(probe):
                raise TypeError("capability probe missing")
            snapshot: Any = probe()
        except UnknownHarnessError:
            reason = "gateway_agent_adapter_missing"
        except Exception:
            reason = "gateway_agent_capability_probe_failed"
        else:
            return evaluate_gateway_cli_compatibility(agent_id, snapshot)
        return _blocked(agent_id, contract.harness_id, contract.version_window, reason)

    return resolve


def gateway_agent_compatibility_to_dict(
    decision: GatewayAgentCompatibilityDecisionV1,
) -> dict[str, Any]:
    """Serialize one redaction-safe decision with explicit no-fallback facts."""
    return {
        "schema_version": GATEWAY_COMPATIBILITY_SCHEMA_VERSION,
        "status": decision.status,
        "agent_id": decision.agent_id,
        "harness_id": decision.harness_id,
        "reason_id": decision.reason_id,
        "expected_version_window": decision.expected_version_window,
        "observed_version": decision.observed_version,
        "missing_capabilities": list(decision.missing_capabilities),
        "fallback_allowed": decision.fallback_allowed,
        "provider_traffic": False,
        "agent_execution_started": False,
    }


def _blocked(
    agent_id: str,
    harness_id: str,
    expected_version_window: str,
    reason_id: str,
    observed_version: str | None = None,
    missing_capabilities: tuple[str, ...] = (),
) -> GatewayAgentCompatibilityDecisionV1:
    return GatewayAgentCompatibilityDecisionV1(
        agent_id=agent_id,
        harness_id=harness_id,
        status="blocked",
        reason_id=reason_id,
        expected_version_window=expected_version_window,
        observed_version=observed_version,
        missing_capabilities=missing_capabilities,
    )


def _missing_contract(agent_id: str) -> GatewayAgentCompatibilityDecisionV1:
    return _blocked(
        agent_id,
        "unknown",
        "unavailable",
        "gateway_agent_compatibility_contract_missing",
    )


def _parsed_version(value: str) -> str | None:
    match = _VERSION_PATTERN.search(value)
    return match.group(1) if match is not None else None


def _version_admitted(
    version: str,
    contract: GatewayAgentCompatibilityContractV1,
) -> bool:
    if contract.pinned_version is not None and version != contract.pinned_version:
        return False
    observed = _release_tuple(version)
    minimum = _release_tuple(contract.minimum_version)
    maximum = _release_tuple(contract.maximum_version_exclusive)
    return (
        observed is not None
        and minimum is not None
        and maximum is not None
        and minimum <= observed < maximum
    )


def _release_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_PATTERN.fullmatch(value)
    if match is None:
        return None
    major, minor, patch = match.group(1).split(".")
    return int(major), int(minor), int(patch)


__all__ = [
    "ACP_SDK_DISTRIBUTION",
    "ACP_SDK_VERSION",
    "ACP_WIRE_PROTOCOL_VERSION",
    "GATEWAY_AGENT_COMPATIBILITY_CONTRACTS",
    "GatewayAgentCompatibilityContractV1",
    "GatewayAgentCompatibilityDecisionV1",
    "GatewayAgentCompatibilityResolver",
    "build_gateway_agent_compatibility_resolver",
    "evaluate_gateway_acp_compatibility",
    "evaluate_gateway_cli_compatibility",
    "gateway_agent_compatibility_to_dict",
]
