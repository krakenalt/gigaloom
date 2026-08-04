"""One-command route discovery, preflight, overlay, and native handoff."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Protocol
from gigaloom.cli_commands.gateway_launch import (
    GatewayLaunchRequestV1,
    gateway_launch_resolution_to_dict,
    resolve_gateway_launch_request,
)
from gigaloom.cli_commands.gateway_compatibility import (
    GatewayAgentCompatibilityResolver,
    build_gateway_agent_compatibility_resolver,
    gateway_agent_compatibility_to_dict,
)
from gigaloom.cli_commands.gateway_transport import (
    AuthenticatedGatewayMachineTransport,
    is_managed_gateway_endpoint,
)
from gigaloom.config import HarnessConfig
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.harnesses.api import build_safe_env
from gigaloom.native.launch.gateway_codec import (
    gateway_preflight_receipt_to_dict,
)
from gigaloom.native.launch.gateway_contracts import (
    BridgeRouteV1,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
)
from gigaloom.native.launch.gateway_discovery import (
    MAX_GATEWAY_DISCOVERY_BYTES,
    GatewayDiscoveryResult,
    GatewayRouteDiscovery,
)
from gigaloom.native.launch.gateway_environment import (
    managed_gpt2giga_environment,
)
from gigaloom.native.launch.gateway_injection import (
    build_gateway_agent_injection,
)
from gigaloom.native.launch.gateway_profile import (
    GPT2GIGA_INSPECT_CONTRACT_REVISION,
    GPT2GIGA_LOSS_MATRIX_REVISION,
    GPT2GIGA_PROVIDER_PROFILE_REVISION,
    resolve_installed_gpt2giga_artifact,
    reviewed_gpt2giga_profile,
)
from gigaloom.native.launch.gateway_sidecar import (
    GatewayArtifactEvidenceV1,
    GatewaySidecarStatus,
    ManagedGatewayLeaseV1,
    ManagedGatewaySidecarService,
    UrlLibGatewayStartupReadinessProbe,
    gateway_artifact_admitted,
)
from gigaloom.native.process import NativeProcessManager


GatewayNativeLauncher = Callable[[tuple[str, ...], Mapping[str, str]], int]
GatewayArtifactResolver = Callable[[GatewayProfileV1], GatewayArtifactEvidenceV1 | None]
GatewayStartupInspector = Callable[
    [GatewayProfileV1, GatewayArtifactEvidenceV1, Mapping[str, str]], str | None
]
_SECRET_ENV_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class GatewaySidecarPort(Protocol):
    """Managed lifecycle needed only for the duration of one native launch."""

    def ensure_started(
        self,
        profile: GatewayProfileV1,
        artifact: GatewayArtifactEvidenceV1,
        *,
        environment: Mapping[str, str],
        session_id: str,
        run_id: str,
    ) -> ManagedGatewayLeaseV1: ...

    def status(self, profile: GatewayProfileV1) -> ManagedGatewayLeaseV1: ...

    def stop(self, profile: GatewayProfileV1) -> ManagedGatewayLeaseV1: ...


@dataclass
class GatewayLaunchApplication:
    """Compose one reviewed gateway route without fallback authority."""

    config: HarnessConfig
    profile: GatewayProfileV1
    discovery: GatewayRouteDiscovery
    artifact_resolver: GatewayArtifactResolver
    managed_root: Path
    gateway_api_key: str
    compatibility_resolver: GatewayAgentCompatibilityResolver
    sidecar: GatewaySidecarPort | None = None
    process_manager: NativeProcessManager | None = None
    startup_inspector: GatewayStartupInspector | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    _startup_inspection_cache: dict[tuple[object, ...], str | None] = field(
        default_factory=dict, init=False, repr=False
    )

    def run(
        self,
        request: GatewayLaunchRequestV1,
        *,
        native_launcher: GatewayNativeLauncher,
    ) -> int:
        """Run discovery through native handoff and clean up owned sidecars."""
        if (
            request.gateway_id is not None
            and request.gateway_id != self.profile.gateway_id
        ):
            self._emit_refusal(request, "gateway_not_configured")
            return 2

        if not request.dry_run:
            compatibility = self.compatibility_resolver(request.agent_id)
            if not compatibility.ready:
                payload = gateway_agent_compatibility_to_dict(compatibility)
                payload.update(
                    route_id=request.route_id,
                    gateway_id=request.gateway_id,
                    reason_ids=[compatibility.reason_id],
                    process_spawn=False,
                )
                _emit(payload, as_json=request.json_output)
                return 2

        artifact = (
            self.artifact_resolver(self.profile)
            if self.profile.mode is GatewayMode.MANAGED
            else None
        )
        if request.dry_run:
            return self._dry_run(request, artifact=artifact)

        lease: ManagedGatewayLeaseV1 | None = None
        sidecar_started = False
        try:
            if self.profile.mode is GatewayMode.MANAGED:
                if not gateway_artifact_admitted(self.profile, artifact):
                    self._emit_refusal(
                        request,
                        (
                            artifact.reason_id
                            if artifact is not None and artifact.reason_id is not None
                            else "gateway_artifact_unverified"
                        ),
                    )
                    return 2
                assert artifact is not None
                if self.sidecar is None:
                    self._emit_refusal(request, "gateway_sidecar_unavailable")
                    return 2
                current_lease = self.sidecar.status(self.profile)
                if _lease_matches_artifact(current_lease, artifact):
                    lease = current_lease
                else:
                    try:
                        sidecar_environment = managed_gpt2giga_environment(
                            self.config,
                            managed_root=self.managed_root,
                            gateway_api_key=self.gateway_api_key,
                            public_model_alias=request.public_model_alias,
                        )
                    except ValueError as error:
                        self._emit_refusal(request, str(error))
                        return 2
                    self.managed_root.mkdir(parents=True, exist_ok=True)
                    self.managed_root.chmod(0o700)
                    reason = self._inspect_startup(
                        artifact,
                        sidecar_environment,
                    )
                    if reason is not None:
                        self._emit_refusal(request, reason)
                        return 2
                    lease = self.sidecar.ensure_started(
                        self.profile,
                        artifact,
                        environment=sidecar_environment,
                        session_id="gateway-launch",
                        run_id=f"gateway-{self.profile.gateway_id}",
                    )
                if (
                    lease.status
                    not in {
                        GatewaySidecarStatus.STARTED,
                        GatewaySidecarStatus.REUSED,
                    }
                    or not lease.readiness_confirmed
                ):
                    reason = (
                        lease.reason.value
                        if lease.reason is not None
                        else "gateway_sidecar_not_ready"
                    )
                    self._emit_refusal(request, reason)
                    return 2
                sidecar_started = lease.status is GatewaySidecarStatus.STARTED

            discovered = self.discovery.discover(self.profile)
            resolution = resolve_gateway_launch_request(
                request,
                discovered,
                profile=self.profile,
                interactive=False,
                now=self._now(),
            )
            if not resolution.ready:
                _emit(
                    gateway_launch_resolution_to_dict(resolution),
                    as_json=request.json_output,
                )
                return 2
            assert resolution.route is not None
            assert resolution.resolved_route is not None
            preflight = _preflight_receipt(
                self.profile,
                resolution.route,
                discovered,
                artifact=artifact,
                lease=lease,
                now=self._now(),
            )
            injection = build_gateway_agent_injection(
                resolution.resolved_route,
                request.agent_id,
                self.profile,
                preflight,
                managed_root=self.managed_root,
                process_lease_ref=(
                    lease.process_lease_ref if lease is not None else None
                ),
            )
            if not injection.ready or injection.overlay is None:
                _emit(
                    {
                        "schema_version": 1,
                        "status": injection.status.value,
                        "agent_id": request.agent_id,
                        "route_id": injection.route_id,
                        "reason_ids": list(injection.reason_ids),
                        "preflight": gateway_preflight_receipt_to_dict(preflight),
                        "process_spawn": False,
                    },
                    as_json=request.json_output,
                )
                return 2
            actual = {
                name: (
                    self.gateway_api_key if projected == "<secret-ref>" else projected
                )
                for name, projected in injection.overlay.redacted_env_delta
            }
            environment = build_safe_env(self.config.to_context(), extra=actual)
            argv = (
                request.agent_id,
                *injection.command_args,
                *request.agent_args,
            )
            return native_launcher(argv, environment)
        finally:
            if sidecar_started and self.sidecar is not None:
                self.sidecar.stop(self.profile)
            if self.process_manager is not None:
                self.process_manager.close(terminate_owned=False)

    def _dry_run(
        self,
        request: GatewayLaunchRequestV1,
        *,
        artifact: GatewayArtifactEvidenceV1 | None,
    ) -> int:
        discovery = self.discovery.discover(self.profile)
        resolution = resolve_gateway_launch_request(
            request,
            discovery,
            profile=self.profile,
            interactive=False,
            now=self._now(),
        )
        payload = gateway_launch_resolution_to_dict(resolution)
        payload["artifact_state"] = (
            "verified"
            if gateway_artifact_admitted(self.profile, artifact)
            else (
                "not_applicable"
                if self.profile.mode is GatewayMode.EXTERNAL
                else "unverified"
            )
        )
        _emit(payload, as_json=request.json_output)
        return 0 if resolution.ready else 2

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("gateway launch clock must be timezone-aware")
        return value

    def _inspect_startup(
        self,
        artifact: GatewayArtifactEvidenceV1,
        environment: Mapping[str, str],
    ) -> str | None:
        if self.startup_inspector is None:
            return None
        try:
            executable = Path(artifact.executable_path).resolve(strict=True)
            stat = executable.stat()
        except OSError:
            return "startup_contract_unavailable"
        key = (
            os.fspath(executable),
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
            artifact.artifact_sha256,
            self.profile.startup_config_revision,
            _environment_fingerprint(environment),
        )
        if key not in self._startup_inspection_cache:
            self._startup_inspection_cache[key] = self.startup_inspector(
                self.profile,
                artifact,
                environment,
            )
        return self._startup_inspection_cache[key]

    @staticmethod
    def _emit_refusal(request: GatewayLaunchRequestV1, reason: str) -> None:
        _emit(
            {
                "schema_version": 1,
                "status": "blocked",
                "agent_id": request.agent_id,
                "route_id": request.route_id,
                "gateway_id": request.gateway_id,
                "reason_ids": [reason],
                "provider_traffic": False,
                "process_spawn": False,
            },
            as_json=request.json_output,
        )


def build_gateway_launch_application(config: HarnessConfig) -> GatewayLaunchApplication:
    """Construct the production gpt2giga runtime from existing process owners."""
    mode = (
        GatewayMode.MANAGED
        if config.auto_start_proxy and is_managed_gateway_endpoint(config.proxy_url)
        else GatewayMode.EXTERNAL
    )
    profile = reviewed_gpt2giga_profile(base_url=config.proxy_url, mode=mode)
    api_key = config.api_key or secrets.token_urlsafe(32)
    transport = AuthenticatedGatewayMachineTransport(api_key)
    discovery = GatewayRouteDiscovery(
        transport,
        credential_fingerprint=transport.credential_fingerprint,
    )
    root = Path(config.data_dir).expanduser().resolve() / "native" / "gateway-runtime"
    manager: NativeProcessManager | None = None
    sidecar: ManagedGatewaySidecarService | None = None
    if mode is GatewayMode.MANAGED:
        manager = NativeProcessManager(use_pty=False)
        readiness = UrlLibGatewayStartupReadinessProbe(transport)
        sidecar = ManagedGatewaySidecarService(
            manager,
            readiness,
            managed_data_root=root,
            startup_timeout_seconds=config.proxy_start_timeout_seconds,
        )
    return GatewayLaunchApplication(
        config=config,
        profile=profile,
        discovery=discovery,
        artifact_resolver=resolve_installed_gpt2giga_artifact,
        managed_root=root,
        gateway_api_key=api_key
        if mode is GatewayMode.MANAGED
        else config.api_key or "0",
        compatibility_resolver=build_gateway_agent_compatibility_resolver(),
        sidecar=sidecar,
        process_manager=manager,
        startup_inspector=inspect_gpt2giga_startup,
    )


def inspect_gpt2giga_startup(
    profile: GatewayProfileV1,
    artifact: GatewayArtifactEvidenceV1,
    environment: Mapping[str, str],
) -> str | None:
    """Verify the public startup manifest without provider or private imports."""
    try:
        completed = subprocess.run(
            (artifact.executable_path, "--inspect-config"),
            env=dict(environment),
            cwd=os.fspath(Path(environment["HOME"]).parent),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (KeyError, OSError, subprocess.SubprocessError):
        return "startup_contract_unavailable"
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8")) > MAX_GATEWAY_DISCOVERY_BYTES
    ):
        return "startup_contract_unavailable"
    try:
        manifest = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "startup_contract_invalid"
    if not isinstance(manifest, Mapping):
        return "startup_contract_invalid"
    expected = {
        "schema_version": GPT2GIGA_INSPECT_CONTRACT_REVISION,
        "profile_schema_version": GPT2GIGA_PROVIDER_PROFILE_REVISION,
        "valid": True,
        "config_revision": profile.startup_config_revision,
        "matrix_revision": GPT2GIGA_LOSS_MATRIX_REVISION,
    }
    if any(manifest.get(name) != value for name, value in expected.items()):
        return "startup_contract_mismatch"
    return None


def _environment_fingerprint(environment: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            name: (
                "<secret>"
                if any(part in name.upper() for part in _SECRET_ENV_PARTS)
                else value
            )
            for name, value in sorted(environment.items())
        }
    )


def _lease_matches_artifact(
    lease: ManagedGatewayLeaseV1,
    artifact: GatewayArtifactEvidenceV1,
) -> bool:
    return bool(
        lease.status is GatewaySidecarStatus.REUSED
        and lease.readiness_confirmed
        and lease.process_lease_ref is not None
        and lease.observed_artifact_sha256 == artifact.artifact_sha256
    )


def _preflight_receipt(
    profile: GatewayProfileV1,
    route: BridgeRouteV1,
    discovery: GatewayDiscoveryResult,
    *,
    artifact: GatewayArtifactEvidenceV1 | None,
    lease: ManagedGatewayLeaseV1 | None,
    now: datetime,
) -> GatewayPreflightReceiptV1:
    catalog = discovery.catalog
    if catalog is None or route not in catalog.routes:
        raise ValueError("gateway route is not current")
    if profile.mode is GatewayMode.MANAGED and (
        not gateway_artifact_admitted(profile, artifact)
        or lease is None
        or not lease.readiness_confirmed
        or lease.status
        not in {GatewaySidecarStatus.STARTED, GatewaySidecarStatus.REUSED}
        or lease.observed_artifact_sha256 != artifact.artifact_sha256
    ):
        raise ValueError("managed gateway preflight is not ready")
    observed_artifact_sha256 = (
        artifact.artifact_sha256
        if profile.mode is GatewayMode.MANAGED and artifact is not None
        else profile.artifact_sha256
    )
    receipt = GatewayPreflightReceiptV1(
        receipt_id="pending",
        gateway_id=profile.gateway_id,
        route_id=route.route_id,
        profile_digest=profile.profile_digest,
        artifact_sha256=observed_artifact_sha256,
        capability_revision=route.capability_profile_revision,
        models_revision=catalog.models_revision,
        loss_matrix_revision=route.loss_matrix_revision,
        support_status=route.support_status,
        status=GatewayPreflightStatus.READY,
        reason_ids=route.reason_ids,
        checked_at=now.isoformat(),
    )
    semantic = gateway_preflight_receipt_to_dict(receipt)
    for name in ("schema_version", "receipt_id", "status", "reason_ids"):
        del semantic[name]
    return replace(
        receipt,
        receipt_id=f"gateway-preflight-{canonical_digest(semantic)[:24]}",
    )


def _emit(payload: Mapping[str, Any], *, as_json: bool) -> None:
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    stream = sys.stdout if as_json else sys.stderr
    stream.write(serialized + "\n")


__all__ = [
    "GatewayLaunchApplication",
    "build_gateway_launch_application",
    "inspect_gpt2giga_startup",
]
