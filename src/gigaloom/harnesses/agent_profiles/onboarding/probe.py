"""Initialize-only conformance probe for generated managed ACP routes."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol, cast, runtime_checkable

from gigaloom.contracts import ManagedAgentArtifactV1
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.acp import (
    AcpLimits,
    AcpProviderBridgeResolution,
    AcpProviderV1,
    AcpRouteIdentity,
    create_acp_client,
    list_providers,
    pin_acp_process,
    resolve_provider_bridge,
)
from gigaloom.harnesses.acp.process import AcpTransportFactory
from gigaloom.harnesses.acp.errors import (
    AcpError,
    AcpProtocolError,
    AcpProtocolVersionError,
)
from gigaloom.harnesses.agent_profiles.models import AgentProfileV1
from gigaloom.harnesses.agent_profiles.onboarding.models import (
    ManagedAcpProbeReceipt,
    ManagedAcpProviderBridgeProjection,
    ManagedProbeState,
    unknown_provider_bridge_projection,
)
from gigaloom.harnesses.agent_profiles.onboarding.isolation import (
    ManagedAcpNetworkIsolationPort,
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

    def __init__(
        self,
        isolation: ManagedAcpNetworkIsolationPort | None,
        *,
        limits: AcpLimits | None = None,
    ) -> None:
        self._isolation = isolation
        self._limits = limits or AcpLimits()

    def probe(
        self,
        profile: AgentProfileV1,
        artifact: ManagedAgentArtifactV1,
        *,
        network_isolated: bool,
    ) -> ManagedAcpProbeReceipt:
        """Initialize once, project capabilities, and always close the process."""
        if not network_isolated or self._isolation is None:
            raise ValueError("managed ACP probe requires enforced network isolation")
        route = _managed_route(profile)
        executable = (
            Path(artifact.managed_root) / artifact.executable_relative_path
        ).resolve(strict=False)
        if not executable.is_relative_to(Path(artifact.managed_root).resolve()):
            raise ValueError("managed ACP executable escapes the artifact root")
        process_fingerprint = artifact.artifact_digest
        executable_observed = False
        providers: tuple[AcpProviderV1, ...] = ()
        provider_list_failed = False
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
                isolated_command = self._isolation.wrap(
                    spec.command,
                    workspace=workspace,
                    native_home=native_home,
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
                    transport_factory=AcpTransportFactory(
                        spec,
                        self._limits,
                        launch_command=isolated_command,
                    ),
                )
                try:
                    client.start()
                    snapshot = client.initialize()
                    if "provider_configuration" in {
                        item.feature for item in snapshot.negotiated_features
                    }:
                        try:
                            providers = list_providers(client)
                        except (AcpError, StructuredProcessError, ValueError):
                            provider_list_failed = True
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
        provider_bridge = _provider_bridge_projection(
            artifact,
            providers_advertised="provider_configuration" in capabilities,
            providers=providers,
            list_failed=provider_list_failed,
        )
        if provider_bridge.status == "blocked":
            if state is ManagedProbeState.READY:
                state = ManagedProbeState.DEGRADED
            warnings = tuple(sorted({*warnings, *provider_bridge.reason_ids}))
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
            "network_policy": "enforced_loopback_only",
            "provider_bridge": provider_bridge.projection(),
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
            network_policy="enforced_loopback_only",
            receipt_digest=canonical_digest(payload),
            provider_bridge=provider_bridge,
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
        "provider_bridge": value.provider_bridge.projection(),
        "session_created": value.session_created,
        "prompt_sent": value.prompt_sent,
        "content_free": value.content_free,
    }


def managed_probe_from_dict(value: Mapping[str, object]) -> ManagedAcpProbeReceipt:
    """Strictly restore one content-free probe receipt from private state."""
    legacy_fields = {
        "state",
        "protocol_state",
        "protocol_version",
        "capability_snapshot_digest",
        "process_fingerprint",
        "executable_observed",
        "handshake_digest",
        "auth_methods",
        "capabilities",
        "losses",
        "warnings",
        "native_home_isolated",
        "network_policy",
        "receipt_digest",
        "session_created",
        "prompt_sent",
        "content_free",
    }
    current_fields = legacy_fields | {"provider_bridge"}
    if frozenset(value) not in {
        frozenset(legacy_fields),
        frozenset(current_fields),
    }:
        raise ValueError("managed probe state fields are invalid")
    provider_bridge = (
        _provider_bridge_from_dict(value["provider_bridge"])
        if "provider_bridge" in value
        else unknown_provider_bridge_projection()
    )
    return ManagedAcpProbeReceipt(
        state=ManagedProbeState(_string(value["state"])),
        protocol_state=_string(value["protocol_state"]),
        protocol_version=_optional_string(value["protocol_version"]),
        capability_snapshot_digest=_optional_string(
            value["capability_snapshot_digest"]
        ),
        process_fingerprint=_string(value["process_fingerprint"]),
        executable_observed=_boolean(value["executable_observed"]),
        handshake_digest=_string(value["handshake_digest"]),
        auth_methods=_string_tuple(value["auth_methods"]),
        capabilities=_string_tuple(value["capabilities"]),
        losses=_string_tuple(value["losses"]),
        warnings=_string_tuple(value["warnings"]),
        native_home_isolated=_boolean(value["native_home_isolated"]),
        network_policy=_string(value["network_policy"]),
        receipt_digest=_string(value["receipt_digest"]),
        provider_bridge=provider_bridge,
        session_created=_boolean(value["session_created"]),
        prompt_sent=_boolean(value["prompt_sent"]),
        content_free=_boolean(value["content_free"]),
    )


def _failure_receipt(
    *,
    state: ManagedProbeState,
    protocol_state: str,
    protocol_version: str | None,
    process_fingerprint: str,
    executable_observed: bool,
    reason_code: str,
) -> ManagedAcpProbeReceipt:
    provider_bridge = _blocked_provider_bridge(reason_code)
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
        "network_policy": "enforced_loopback_only",
        "provider_bridge": provider_bridge.projection(),
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
        network_policy="enforced_loopback_only",
        receipt_digest=canonical_digest(payload),
        provider_bridge=provider_bridge,
    )


def _provider_bridge_projection(
    artifact: ManagedAgentArtifactV1,
    *,
    providers_advertised: bool,
    providers: tuple[AcpProviderV1, ...],
    list_failed: bool,
) -> ManagedAcpProviderBridgeProjection:
    if list_failed:
        return _blocked_provider_bridge("acp_provider_contract_regression")
    provider_ids = tuple(sorted({item.provider_id for item in providers}))
    protocols = tuple(
        sorted(
            {
                protocol
                for item in providers
                for api_type in item.supported_api_types
                for protocol in _provider_protocols(api_type)
            }
        )
    )
    if providers_advertised and not providers:
        return _blocked_provider_bridge("acp_provider_list_empty")
    if providers_advertised and not protocols:
        return ManagedAcpProviderBridgeProjection(
            status="blocked",
            strategy=None,
            protocols=(),
            provider_ids=provider_ids,
            adapter_id=None,
            adapter_revision=None,
            model_selection=None,
            reason_ids=("acp_provider_protocol_unknown",),
        )
    resolution = resolve_provider_bridge(
        registry_id=artifact.registry_id,
        version=artifact.version,
        providers_advertised=providers_advertised,
        advertised_provider_protocols=protocols,
    )
    return _provider_bridge_from_resolution(
        resolution,
        provider_ids=provider_ids,
    )


def _provider_bridge_from_resolution(
    value: AcpProviderBridgeResolution,
    *,
    provider_ids: tuple[str, ...],
) -> ManagedAcpProviderBridgeProjection:
    return ManagedAcpProviderBridgeProjection(
        status=value.status.value,
        strategy=value.strategy.value if value.strategy is not None else None,
        protocols=value.provider_protocols,
        provider_ids=provider_ids,
        adapter_id=value.adapter_id,
        adapter_revision=value.adapter_revision,
        model_selection=value.model_selection,
        reason_ids=value.reason_ids,
    )


def _blocked_provider_bridge(reason_id: str) -> ManagedAcpProviderBridgeProjection:
    return ManagedAcpProviderBridgeProjection(
        status="blocked",
        strategy=None,
        protocols=(),
        provider_ids=(),
        adapter_id=None,
        adapter_revision=None,
        model_selection=None,
        reason_ids=(reason_id,),
    )


def _provider_protocols(api_type: str) -> tuple[str, ...]:
    return {
        "openai": ("openai_chat_completions", "openai_responses"),
        "anthropic": ("anthropic_messages",),
        "gemini": ("gemini_generate_content",),
    }.get(api_type, ())


def _provider_bridge_from_dict(
    value: object,
) -> ManagedAcpProviderBridgeProjection:
    if not isinstance(value, Mapping):
        raise ValueError("managed ACP provider bridge state must be an object")
    expected = {
        "status",
        "strategy",
        "protocols",
        "provider_ids",
        "adapter_id",
        "adapter_revision",
        "model_selection",
        "reason_ids",
    }
    if set(value) != expected:
        raise ValueError("managed ACP provider bridge state fields are invalid")
    return ManagedAcpProviderBridgeProjection(
        status=_string(value["status"]),
        strategy=_optional_string(value["strategy"]),
        protocols=_string_tuple(value["protocols"]),
        provider_ids=_string_tuple(value["provider_ids"]),
        adapter_id=_optional_string(value["adapter_id"]),
        adapter_revision=_optional_string(value["adapter_revision"]),
        model_selection=_optional_string(value["model_selection"]),
        reason_ids=_string_tuple(value["reason_ids"]),
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


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("managed probe state value must be text")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("managed probe state value must be boolean")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("managed probe state value must be a string array")
    return tuple(cast(list[str], value))
