"""Content-free CLI projections for reviewed gateway profiles."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Protocol

from gigaloom.config import HarnessConfig
from gigaloom.native.api import (
    GatewayArtifactEvidenceV1,
    GatewayDiscoveryStatus,
    GatewayMode,
    GatewayProfileV1,
    GatewayRouteDiscovery,
    GatewaySidecarStatus,
    GatewayStartupReadinessProbe,
    ManagedGatewayLeaseV1,
    bridge_route_to_dict,
    gateway_profile_to_dict,
)


GatewayArtifactResolver = Callable[[GatewayProfileV1], GatewayArtifactEvidenceV1 | None]
GatewayCommandServiceFactory = Callable[[HarnessConfig], "GatewayCommandService"]
_SERVICE_FACTORY: GatewayCommandServiceFactory | None = None


class GatewaySidecarOperator(Protocol):
    """Existing managed lease operations needed by route-local controls."""

    def ensure_started(
        self,
        profile: GatewayProfileV1,
        artifact: GatewayArtifactEvidenceV1,
        *,
        environment: Mapping[str, str],
        session_id: str,
        run_id: str,
    ) -> ManagedGatewayLeaseV1: ...

    def stop(self, profile: GatewayProfileV1) -> ManagedGatewayLeaseV1: ...


@dataclass(frozen=True)
class GatewayCommandService:
    """Compose profile, machine-contract, health, and lease owners for CLI use."""

    profiles: tuple[GatewayProfileV1, ...]
    discovery: GatewayRouteDiscovery
    readiness_probe: GatewayStartupReadinessProbe
    artifact_resolver: GatewayArtifactResolver
    sidecar: GatewaySidecarOperator | None = None

    def __post_init__(self) -> None:
        identifiers = [item.gateway_id for item in self.profiles]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("gateway profile ids must be unique")

    @classmethod
    def create(
        cls,
        profiles: Iterable[GatewayProfileV1],
        *,
        discovery: GatewayRouteDiscovery,
        readiness_probe: GatewayStartupReadinessProbe,
        artifact_resolver: GatewayArtifactResolver,
        sidecar: GatewaySidecarOperator | None = None,
    ) -> "GatewayCommandService":
        """Build one deterministic service from composition-owned dependencies."""
        return cls(
            tuple(sorted(profiles, key=lambda item: item.gateway_id)),
            discovery,
            readiness_probe,
            artifact_resolver,
            sidecar,
        )

    def list(self) -> dict[str, Any]:
        """List reviewed profiles without network or process activity."""
        return {
            "schema_version": 1,
            "gateways": [_profile_summary(item) for item in self.profiles],
        }

    def inspect(self, gateway_id: str, *, refresh: bool) -> dict[str, Any]:
        """Read public health/models/capability facts without inference traffic."""
        profile = self._profile(gateway_id)
        result = self.discovery.discover(profile, force_refresh=refresh)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "profile": gateway_profile_to_dict(profile),
            "discovery_status": result.status.value,
            "reason_ids": [item.value for item in result.reason_ids],
            "catalog": None,
        }
        if result.catalog is not None:
            catalog = result.catalog
            payload["catalog"] = {
                "gateway_id": catalog.gateway_id,
                "profile_digest": catalog.profile_digest,
                "models_revision": catalog.models_revision,
                "capabilities_revision": catalog.capabilities_revision,
                "loss_matrix_revision": catalog.loss_matrix_revision,
                "discovered_at": catalog.discovered_at,
                "expires_at": catalog.expires_at,
                "catalog_digest": catalog.catalog_digest,
                "routes": [bridge_route_to_dict(item) for item in catalog.routes],
            }
        return payload

    def doctor(self, gateway_id: str) -> dict[str, Any]:
        """Check pinned artifact and health only; never query models or providers."""
        profile = self._profile(gateway_id)
        artifact = (
            self.artifact_resolver(profile)
            if profile.mode is GatewayMode.MANAGED
            else None
        )
        artifact_state = _artifact_state(profile, artifact)
        health_ready = self.readiness_probe.startup_ready(profile.base_url)
        ready = health_ready and (
            profile.mode is GatewayMode.EXTERNAL or artifact_state == "verified"
        )
        return {
            "schema_version": 1,
            "gateway_id": profile.gateway_id,
            "profile_digest": profile.profile_digest,
            "mode": profile.mode.value,
            "artifact_state": artifact_state,
            "artifact_sha256": profile.artifact_sha256,
            "health_ready": health_ready,
            "ready": ready,
            "provider_inference_performed": False,
        }

    def start(
        self,
        gateway_id: str,
        *,
        environment: Mapping[str, str],
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """Acquire or warm-reuse the existing owner lease for one managed profile."""
        profile = self._profile(gateway_id)
        if self.sidecar is None:
            raise RuntimeError("gateway sidecar service is not configured")
        artifact = self.artifact_resolver(profile)
        if artifact is None:
            raise RuntimeError("gateway artifact evidence is unavailable")
        return _lease_payload(
            self.sidecar.ensure_started(
                profile,
                artifact,
                environment=environment,
                session_id=session_id,
                run_id=run_id,
            )
        )

    def stop(self, gateway_id: str) -> dict[str, Any]:
        """Stop only the exact managed lease owned by this service."""
        profile = self._profile(gateway_id)
        if self.sidecar is None:
            raise RuntimeError("gateway sidecar service is not configured")
        return _lease_payload(self.sidecar.stop(profile))

    def _profile(self, gateway_id: str) -> GatewayProfileV1:
        for profile in self.profiles:
            if profile.gateway_id == gateway_id:
                return profile
        raise ValueError(f"unknown gateway profile: {gateway_id}")


@dataclass(frozen=True)
class GatewayCommandHandlers:
    """Bound route-local handlers used by tests and final CLI composition."""

    service: GatewayCommandService

    def list(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        _emit(self.service.list(), as_json=args.json)
        return 0

    def inspect(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.service.inspect(args.gateway_id, refresh=args.refresh)
        _emit(payload, as_json=args.json)
        return (
            0
            if payload["discovery_status"] == GatewayDiscoveryStatus.CURRENT.value
            else 2
        )

    def doctor(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.service.doctor(args.gateway_id)
        _emit(payload, as_json=args.json)
        return 0 if payload["ready"] is True else 2

    def start(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.service.start(
            args.gateway_id,
            environment=dict(os.environ),
            session_id="gateway-operator",
            run_id=f"gateway-{args.gateway_id}",
        )
        _emit(payload, as_json=args.json)
        return (
            0
            if payload["status"]
            in {
                GatewaySidecarStatus.STARTED.value,
                GatewaySidecarStatus.REUSED.value,
            }
            else 2
        )

    def stop(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.service.stop(args.gateway_id)
        _emit(payload, as_json=args.json)
        return 0 if payload["status"] == GatewaySidecarStatus.STOPPED.value else 2


def configure_gateway_command_service_factory(
    factory: GatewayCommandServiceFactory | None,
) -> None:
    """Install the integrator-owned service factory without eager side effects."""
    global _SERVICE_FACTORY
    _SERVICE_FACTORY = factory


def _handle_gateway_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    return GatewayCommandHandlers(_service(config)).list(args, config)


def _handle_gateway_inspect(args: argparse.Namespace, config: HarnessConfig) -> int:
    return GatewayCommandHandlers(_service(config)).inspect(args, config)


def _handle_gateway_doctor(args: argparse.Namespace, config: HarnessConfig) -> int:
    return GatewayCommandHandlers(_service(config)).doctor(args, config)


def _handle_gateway_start(args: argparse.Namespace, config: HarnessConfig) -> int:
    return GatewayCommandHandlers(_service(config)).start(args, config)


def _handle_gateway_stop(args: argparse.Namespace, config: HarnessConfig) -> int:
    return GatewayCommandHandlers(_service(config)).stop(args, config)


def _service(config: HarnessConfig) -> GatewayCommandService:
    if _SERVICE_FACTORY is None:
        from gigaloom.cli_commands.gateway_runtime import (
            build_gateway_command_service,
        )

        return build_gateway_command_service(config)
    return _SERVICE_FACTORY(config)


def _profile_summary(profile: GatewayProfileV1) -> dict[str, Any]:
    return {
        "gateway_id": profile.gateway_id,
        "display_name": profile.display_name,
        "mode": profile.mode.value,
        "distribution": profile.distribution,
        "version": profile.version,
        "version_window": profile.version_window,
        "base_url": profile.base_url,
        "profile_digest": profile.profile_digest,
    }


def _artifact_state(
    profile: GatewayProfileV1,
    artifact: GatewayArtifactEvidenceV1 | None,
) -> str:
    if profile.mode is GatewayMode.EXTERNAL:
        return "not_applicable"
    if artifact is None:
        return "unavailable"
    if not artifact.verified:
        return "unverified"
    if (
        artifact.distribution != profile.distribution
        or artifact.version != profile.version
        or artifact.artifact_sha256 != profile.artifact_sha256
    ):
        return "identity_mismatch"
    try:
        executable = Path(artifact.executable_path).resolve(strict=True)
    except OSError:
        return "executable_unavailable"
    if (
        not executable.is_file()
        or not os.access(executable, os.X_OK)
        or executable.name.removesuffix(".exe") != profile.executable
    ):
        return "executable_unavailable"
    return "verified"


def _lease_payload(lease: ManagedGatewayLeaseV1) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "gateway_id": lease.gateway_id,
        "profile_digest": lease.profile_digest,
        "status": lease.status.value,
        "process_lease_ref": lease.process_lease_ref,
        "managed_root": lease.managed_root,
        "startup_config_ref": lease.startup_config_ref,
        "readiness_confirmed": lease.readiness_confirmed,
        "reason": lease.reason.value if lease.reason is not None else None,
    }


def _emit(payload: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return
    for name, value in payload.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, bool):
            rendered = "yes" if value else "no"
        elif value is None:
            rendered = "none"
        else:
            rendered = str(value)
        sys.stdout.write(f"{name.replace('_', ' ').title()}: {rendered}\n")


__all__ = [
    "GatewayCommandHandlers",
    "GatewayCommandService",
    "configure_gateway_command_service_factory",
]
