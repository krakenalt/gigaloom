"""Initialize-only conformance probe for generated managed ACP routes."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol, runtime_checkable

from gigaloom.contracts import ManagedAgentArtifactV1
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.acp import (
    AcpLimits,
    AcpRouteIdentity,
    create_acp_client,
    pin_acp_process,
)
from gigaloom.harnesses.acp.errors import (
    AcpError,
    AcpProtocolError,
    AcpProtocolVersionError,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedProbeState,
)
from gigaloom.structured_processes import StructuredProcessError


_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")


@runtime_checkable
class ManagedAcpProbePort(Protocol):
    """Injected initialize-only probe boundary used by onboarding."""

    def probe(
        self,
        profile: AgentProfileV1,
        artifact: ManagedAgentArtifactV1,
        *,
        network_isolated: bool,
    ) -> ManagedAcpProbeReceipt:
        """Return content-free conformance evidence without a session or prompt."""


class ManagedAcpProbeRunner:
    """Run the generic ACP gateway in disposable HOME/workspace roots."""

    def __init__(self, *, limits: AcpLimits | None = None) -> None:
        self._limits = limits or AcpLimits()

    def probe(
        self,
        profile: AgentProfileV1,
        artifact: ManagedAgentArtifactV1,
        *,
        network_isolated: bool,
    ) -> ManagedAcpProbeReceipt:
        """Initialize once, project capabilities, and always close the process."""
        if not network_isolated:
            raise ValueError("managed ACP probe requires enforced network isolation")
        route = _managed_route(profile)
        executable = (
            Path(artifact.managed_root) / artifact.executable_relative_path
        ).resolve(strict=False)
        if not executable.is_relative_to(Path(artifact.managed_root).resolve()):
            raise ValueError("managed ACP executable escapes the artifact root")
        process_fingerprint = artifact.artifact_digest
        executable_observed = False
        with tempfile.TemporaryDirectory(prefix="gigaloom-managed-acp-probe-") as root:
            probe_root = Path(root)
            workspace = probe_root / "workspace"
            native_home = probe_root / "home"
            workspace.mkdir(mode=0o700)
            native_home.mkdir(mode=0o700)
            environment = {
                "HOME": str(native_home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "TMPDIR": root,
                **dict(artifact.environment),
            }
            try:
                spec = pin_acp_process(
                    (str(executable), *artifact.arguments),
                    cwd=workspace,
                    environment=environment,
                    allowed_environment=frozenset(
                        {"HOME", *dict(artifact.environment)}
                    ),
                )
                process_fingerprint = spec.executable.fingerprint
                executable_observed = True
                client = create_acp_client(
                    spec,
                    compatibility_profile_digest=profile.profile_digest,
                    route_identity=AcpRouteIdentity(
                        profile.agent_id,
                        route.route_id,
                        profile.profile_digest,
                    ),
                    limits=self._limits,
                )
                try:
                    client.start()
                    snapshot = client.initialize()
                finally:
                    client.close()
            except AcpProtocolVersionError:
                return _failure_receipt(
                    state=ManagedProbeState.INCOMPATIBLE,
                    protocol_state="major_mismatch",
                    protocol_version="unknown",
                    process_fingerprint=process_fingerprint,
                    executable_observed=executable_observed,
                    reason_code="protocol_major_mismatch",
                )
            except AcpProtocolError:
                return _failure_receipt(
                    state=ManagedProbeState.UNSAFE,
                    protocol_state="malformed",
                    protocol_version=None,
                    process_fingerprint=process_fingerprint,
                    executable_observed=executable_observed,
                    reason_code="malformed_acp_initialize",
                )
            except (AcpError, StructuredProcessError, OSError, ValueError):
                return _failure_receipt(
                    state=ManagedProbeState.UNAVAILABLE,
                    protocol_state="unavailable",
                    protocol_version=None,
                    process_fingerprint=process_fingerprint,
                    executable_observed=executable_observed,
                    reason_code="acp_initialize_unavailable",
                )
        capabilities = tuple(
            sorted(item.feature for item in snapshot.negotiated_features)
        )
        losses = tuple(sorted(item.feature for item in snapshot.unsupported_features))
        auth_methods = _auth_method_ids(snapshot.auth_capabilities)
        warnings = tuple(
            sorted(
                {
                    *(f"loss_{_safe_identity(item)}" for item in losses),
                    *(("authentication_required",) if auth_methods else ()),
                }
            )
        )
        state = (
            ManagedProbeState.AUTH_REQUIRED
            if auth_methods
            else ManagedProbeState.DEGRADED
            if losses
            else ManagedProbeState.READY
        )
        handshake_digest = canonical_digest(
            {
                "protocol_version": snapshot.protocol_version,
                "capability_snapshot_digest": snapshot.snapshot_digest,
                "process_fingerprint": snapshot.process_fingerprint,
            }
        )
        payload = {
            "state": state.value,
            "protocol_state": "conformant",
            "protocol_version": snapshot.protocol_version,
            "capability_snapshot_digest": snapshot.snapshot_digest,
            "process_fingerprint": snapshot.process_fingerprint,
            "executable_observed": True,
            "handshake_digest": handshake_digest,
            "auth_methods": list(auth_methods),
            "capabilities": list(capabilities),
            "losses": list(losses),
            "warnings": list(warnings),
            "native_home_isolated": True,
            "network_policy": "enforced_deny",
            "session_created": False,
            "prompt_sent": False,
            "content_free": True,
        }
        return ManagedAcpProbeReceipt(
            state=state,
            protocol_state="conformant",
            protocol_version=snapshot.protocol_version,
            capability_snapshot_digest=snapshot.snapshot_digest,
            process_fingerprint=snapshot.process_fingerprint,
            executable_observed=True,
            handshake_digest=handshake_digest,
            auth_methods=auth_methods,
            capabilities=capabilities,
            losses=losses,
            warnings=warnings,
            native_home_isolated=True,
            network_policy="enforced_deny",
            receipt_digest=canonical_digest(payload),
        )


def managed_probe_to_dict(value: ManagedAcpProbeReceipt) -> dict[str, object]:
    """Serialize one bounded content-free probe projection."""
    return {
        "state": value.state.value,
        "protocol_state": value.protocol_state,
        "protocol_version": value.protocol_version,
        "capability_snapshot_digest": value.capability_snapshot_digest,
        "process_fingerprint": value.process_fingerprint,
        "executable_observed": value.executable_observed,
        "handshake_digest": value.handshake_digest,
        "auth_methods": list(value.auth_methods),
        "capabilities": list(value.capabilities),
        "losses": list(value.losses),
        "warnings": list(value.warnings),
        "native_home_isolated": value.native_home_isolated,
        "network_policy": value.network_policy,
        "receipt_digest": value.receipt_digest,
        "session_created": value.session_created,
        "prompt_sent": value.prompt_sent,
        "content_free": value.content_free,
    }


def _failure_receipt(
    *,
    state: ManagedProbeState,
    protocol_state: str,
    protocol_version: str | None,
    process_fingerprint: str,
    executable_observed: bool,
    reason_code: str,
) -> ManagedAcpProbeReceipt:
    handshake_digest = canonical_digest(
        {
            "protocol_state": protocol_state,
            "protocol_version": protocol_version,
            "process_fingerprint": process_fingerprint,
            "reason_code": reason_code,
        }
    )
    payload = {
        "state": state.value,
        "protocol_state": protocol_state,
        "protocol_version": protocol_version,
        "capability_snapshot_digest": None,
        "process_fingerprint": process_fingerprint,
        "executable_observed": executable_observed,
        "handshake_digest": handshake_digest,
        "auth_methods": [],
        "capabilities": [],
        "losses": [],
        "warnings": [reason_code],
        "native_home_isolated": True,
        "network_policy": "enforced_deny",
        "session_created": False,
        "prompt_sent": False,
        "content_free": True,
    }
    return ManagedAcpProbeReceipt(
        state=state,
        protocol_state=protocol_state,
        protocol_version=protocol_version,
        capability_snapshot_digest=None,
        process_fingerprint=process_fingerprint,
        executable_observed=executable_observed,
        handshake_digest=handshake_digest,
        auth_methods=(),
        capabilities=(),
        losses=(),
        warnings=(reason_code,),
        native_home_isolated=True,
        network_policy="enforced_deny",
        receipt_digest=canonical_digest(payload),
    )


def _managed_route(profile: AgentProfileV1):  # noqa: ANN202
    if profile.native is not None or len(profile.structured_routes) != 1:
        raise ValueError("managed ACP profile must be structured-only")
    route = profile.structured_routes[0]
    if route.transport_kind != "acp_stdio_v1":
        raise ValueError("managed ACP profile route is not ACP stdio v1")
    return route


def _auth_method_ids(value: Mapping[str, object]) -> tuple[str, ...]:
    methods = value.get("methods", ())
    if not isinstance(methods, tuple):
        return ()
    result = []
    for position, item in enumerate(methods):
        if not isinstance(item, Mapping):
            continue
        identity = str(item.get("id", ""))
        result.append(
            identity
            if _IDENTITY_RE.fullmatch(identity)
            else f"auth-method-{position + 1}"
        )
    return tuple(sorted(set(result)))


def _safe_identity(value: str) -> str:
    return value if _IDENTITY_RE.fullmatch(value) else "unknown"
