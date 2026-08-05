"""Reviewed gateway route catalog and submission binding for Web."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import secrets
from threading import Lock
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from gigaloom.cli_commands.gateway_transport import (
    AuthenticatedGatewayMachineTransport,
)
from gigaloom.config import HarnessConfig
from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.native.launch.gateway_codec import (
    bridge_route_to_dict,
    gateway_preflight_receipt_to_dict,
)
from gigaloom.native.launch.gateway_contracts import (
    BridgeRouteV1,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewaySupportStatus,
)
from gigaloom.native.launch.gateway_discovery import (
    GatewayDiscoveryStatus,
    GatewayRouteCatalogV1,
    GatewayRouteDiscovery,
)
from gigaloom.native.launch.gateway_environment import (
    managed_gpt2giga_environment,
)
from gigaloom.native.launch.gateway_profile import (
    resolve_installed_gpt2giga_artifact,
    reviewed_gpt2giga_profile,
)
from gigaloom.native.launch.gateway_sidecar import (
    GatewayArtifactEvidenceV1,
    GatewayProcessLeaseOwner,
    GatewaySidecarStatus,
    ManagedGatewayLeaseV1,
    ManagedGatewaySidecarService,
    UrlLibGatewayStartupReadinessProbe,
    gateway_artifact_admitted,
)


_PREFLIGHT_TTL = timedelta(minutes=5)
ArtifactResolver = Callable[[GatewayProfileV1], GatewayArtifactEvidenceV1 | None]
SidecarEnvironmentFactory = Callable[[], Mapping[str, str]]


class GatewayRouteStartError(ValueError):
    """Typed fail-closed refusal for an explicit managed start action."""

    def __init__(self, reason_id: str) -> None:
        super().__init__(reason_id)
        self.reason_id = reason_id


@dataclass(frozen=True, slots=True)
class _IssuedPreflight:
    receipt: GatewayPreflightReceiptV1
    acknowledgement_id: str | None


class GatewayRouteWebService:
    """Own reviewed discovery, preflight receipts, and exact run bindings."""

    def __init__(
        self,
        profile: GatewayProfileV1,
        discovery: GatewayRouteDiscovery,
        *,
        artifact_resolver: ArtifactResolver = resolve_installed_gpt2giga_artifact,
        sidecar: ManagedGatewaySidecarService | None = None,
        sidecar_environment: SidecarEnvironmentFactory | None = None,
        gateway_api_key: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.profile = profile
        self.discovery = discovery
        self.artifact_resolver = artifact_resolver
        self.sidecar = sidecar
        self.sidecar_environment = sidecar_environment
        self.gateway_api_key = gateway_api_key
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._issued: dict[str, _IssuedPreflight] = {}
        self._lifecycle_lock = Lock()

    @classmethod
    def from_config(
        cls,
        config: HarnessConfig,
        *,
        process_owner: GatewayProcessLeaseOwner | None = None,
    ) -> GatewayRouteWebService:
        """Build the one reviewed profile from existing Harness configuration."""
        parsed = urlsplit(config.proxy_url)
        managed = (
            config.auto_start_proxy
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.path.rstrip("/")
        )
        profile = reviewed_gpt2giga_profile(
            base_url=_credential_free_base_url(parsed),
            mode=GatewayMode.MANAGED if managed else GatewayMode.EXTERNAL,
        )
        gateway_api_key = config.api_key or (
            secrets.token_urlsafe(32) if managed else None
        )
        transport = AuthenticatedGatewayMachineTransport(gateway_api_key)
        managed_root = (
            Path(config.data_dir).expanduser().resolve() / "native" / "gateway-runtime"
        )
        sidecar = (
            ManagedGatewaySidecarService(
                process_owner,
                UrlLibGatewayStartupReadinessProbe(transport),
                managed_data_root=managed_root,
                startup_timeout_seconds=config.proxy_start_timeout_seconds,
            )
            if managed and process_owner is not None
            else None
        )
        return cls(
            profile,
            GatewayRouteDiscovery(
                transport,
                credential_fingerprint=transport.credential_fingerprint,
            ),
            sidecar=sidecar,
            sidecar_environment=(
                lambda: managed_gpt2giga_environment(
                    config,
                    managed_root=managed_root,
                    gateway_api_key=gateway_api_key or "0",
                )
            )
            if sidecar is not None
            else None,
            gateway_api_key=gateway_api_key,
        )

    def catalog(self, *, refresh: bool = False) -> dict[str, Any]:
        """Project bounded route facts without provider traffic or fallback."""
        result = self.discovery.discover(self.profile, force_refresh=refresh)
        routes: list[dict[str, Any]] = []
        if result.catalog is not None:
            for route in result.catalog.routes:
                projected = bridge_route_to_dict(route)
                projected["gateway_display_name"] = self.profile.display_name
                routes.append(projected)
        return {
            "status": result.status.value,
            "reason_ids": [reason.value for reason in result.reason_ids],
            "routes": routes,
            "lifecycle": {
                "mode": self.profile.mode.value,
                "start_available": (
                    self.sidecar is not None and self.sidecar_environment is not None
                ),
            },
        }

    def models(self, *, api_mode: str) -> dict[str, Any]:
        """Project dynamic models from the same cached machine contracts."""
        protocols = {
            "v1": "openai_chat_completions",
            "v2": "openai_responses",
        }
        protocol = protocols.get(api_mode)
        if protocol is None:
            raise ValueError("invalid api_mode; expected v1 or v2")
        result = self.discovery.discover(self.profile)
        catalog = result.catalog
        models = (
            sorted(
                {
                    route.public_model_alias
                    for route in catalog.routes
                    if route.client_protocol == protocol
                }
            )
            if catalog is not None
            else []
        )
        return {
            "schema_version": 1,
            "ok": result.status is GatewayDiscoveryStatus.CURRENT,
            "api_mode": api_mode,
            "route_path": f"/{api_mode}/models",
            "health": (
                "ready"
                if result.status is GatewayDiscoveryStatus.CURRENT
                else result.status.value
            ),
            "last_checked_at": (catalog.discovered_at if catalog is not None else None),
            "models": models,
            "source": "gateway_route_catalog",
            "error": (
                None
                if result.status is GatewayDiscoveryStatus.CURRENT
                else "model discovery is not current"
            ),
        }

    def start(self, *, session_id: str) -> dict[str, Any]:
        """Explicitly start or reuse the exact managed sidecar, then rediscover."""
        _validate_session_id(session_id)
        with self._lifecycle_lock:
            return self._start_bound(session_id)

    def _start_bound(self, session_id: str) -> dict[str, Any]:
        if (
            self.profile.mode is not GatewayMode.MANAGED
            or self.sidecar is None
            or self.sidecar_environment is None
        ):
            raise GatewayRouteStartError("gateway_managed_start_unavailable")
        artifact = self.artifact_resolver(self.profile)
        if not gateway_artifact_admitted(self.profile, artifact):
            raise GatewayRouteStartError("gateway_artifact_unverified")
        assert artifact is not None
        try:
            environment = self.sidecar_environment()
        except ValueError as error:
            raise GatewayRouteStartError(_safe_reason_id(error)) from error
        lease = self.sidecar.ensure_started(
            self.profile,
            artifact,
            environment=environment,
            session_id=session_id,
            run_id=f"gateway-route-{self.profile.gateway_id}",
        )
        if (
            not _lease_ready(lease)
            or lease.observed_artifact_sha256 != artifact.artifact_sha256
        ):
            raise GatewayRouteStartError(
                lease.reason.value
                if lease.reason is not None
                else "gateway_sidecar_not_ready"
            )
        catalog = self.catalog(refresh=True)
        if catalog["status"] != GatewayDiscoveryStatus.CURRENT.value:
            raise GatewayRouteStartError("gateway_route_catalog_not_ready")
        return {
            "status": lease.status.value,
            "readiness_confirmed": lease.readiness_confirmed,
            "reason_id": None,
            "catalog": catalog,
        }

    def close(self) -> None:
        """Stop only the exact managed lease owned by this Web service."""
        with self._lifecycle_lock:
            if self.sidecar is not None and self.profile.mode is GatewayMode.MANAGED:
                self.sidecar.stop(self.profile)

    def preflight(
        self,
        route_id: str,
        *,
        acknowledgement_id: str | None,
    ) -> dict[str, Any]:
        """Issue one short-lived receipt for the exact current route facts."""
        result = self.discovery.discover(self.profile)
        if result.status is not GatewayDiscoveryStatus.CURRENT:
            raise ValueError("gateway route capabilities are not current")
        catalog = result.catalog
        assert catalog is not None
        route = _route(catalog, route_id)
        if route.support_status is GatewaySupportStatus.BLOCKED:
            raise ValueError("gateway route is blocked")
        if route.required_acknowledgement != acknowledgement_id:
            raise ValueError("gateway route acknowledgement does not match")
        artifact = self.artifact_resolver(self.profile)
        if self.profile.mode is GatewayMode.MANAGED and not gateway_artifact_admitted(
            self.profile, artifact
        ):
            raise ValueError("managed gateway artifact is not verified")
        observed_artifact_sha256 = (
            artifact.artifact_sha256
            if self.profile.mode is GatewayMode.MANAGED and artifact is not None
            else self.profile.artifact_sha256
        )
        now = _aware(self.clock())
        binding = {
            "gateway_id": self.profile.gateway_id,
            "route_id": route.route_id,
            "profile_digest": self.profile.profile_digest,
            "artifact_sha256": observed_artifact_sha256,
            "capability_revision": route.capability_profile_revision,
            "models_revision": catalog.models_revision,
            "loss_matrix_revision": route.loss_matrix_revision,
            "support_status": route.support_status.value,
            "checked_at": now.isoformat(),
        }
        receipt = GatewayPreflightReceiptV1(
            receipt_id=f"gateway-preflight-{canonical_digest(binding)[:24]}",
            gateway_id=self.profile.gateway_id,
            route_id=route.route_id,
            profile_digest=self.profile.profile_digest,
            artifact_sha256=observed_artifact_sha256,
            capability_revision=route.capability_profile_revision,
            models_revision=catalog.models_revision,
            loss_matrix_revision=route.loss_matrix_revision,
            support_status=route.support_status,
            status=GatewayPreflightStatus.READY,
            reason_ids=route.reason_ids,
            checked_at=now.isoformat(),
        )
        self._issued[receipt.receipt_id] = _IssuedPreflight(
            receipt=receipt,
            acknowledgement_id=acknowledgement_id,
        )
        return gateway_preflight_receipt_to_dict(receipt)

    def bind_submission(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and persist only an exact, still-current reviewed binding."""
        route_id = payload.get("route_id")
        raw_binding = payload.get("gateway_route_binding")
        if route_id is None and raw_binding is None:
            return dict(payload)
        if not isinstance(route_id, str) or not isinstance(raw_binding, Mapping):
            raise ValueError("gateway route binding is incomplete")
        binding = dict(raw_binding)
        receipt_id = binding.get("preflight_receipt_id")
        issued = self._issued.get(str(receipt_id))
        if issued is None:
            raise ValueError("gateway route preflight receipt is unknown")
        checked_at = datetime.fromisoformat(issued.receipt.checked_at)
        if _aware(self.clock()) - checked_at > _PREFLIGHT_TTL:
            self._issued.pop(issued.receipt.receipt_id, None)
            raise ValueError("gateway route preflight receipt expired")
        result = self.discovery.discover(self.profile)
        if result.status is not GatewayDiscoveryStatus.CURRENT:
            raise ValueError("gateway route capabilities are not current")
        catalog = result.catalog
        assert catalog is not None
        route = _route(catalog, route_id)
        expected = {
            "schema_version": 1,
            "route_id": route.route_id,
            "agent_id": route.agent_id,
            "gateway_profile_id": self.profile.gateway_id,
            "public_model_alias": route.public_model_alias,
            "support_status": route.support_status.value,
            "acknowledgement_id": issued.acknowledgement_id,
            "preflight_receipt_id": issued.receipt.receipt_id,
            "preflight_checked_at": issued.receipt.checked_at,
            "profile_digest": self.profile.profile_digest,
            "artifact_sha256": issued.receipt.artifact_sha256,
            "capability_profile_revision": route.capability_profile_revision,
            "models_revision": catalog.models_revision,
            "loss_matrix_revision": route.loss_matrix_revision,
        }
        if binding != expected:
            raise ValueError("gateway route binding no longer matches preflight")
        bound = dict(payload)
        extra = dict(bound.get("extra") or {})
        extra["gateway_route_binding"] = expected
        bound["extra"] = extra
        bound["gateway_route_binding"] = expected
        return bound


def _route(catalog: GatewayRouteCatalogV1, route_id: str) -> BridgeRouteV1:
    for route in catalog.routes:
        if route.route_id == route_id:
            return route
    raise ValueError("gateway route is not current")


def _lease_ready(lease: ManagedGatewayLeaseV1) -> bool:
    return bool(
        lease.status in {GatewaySidecarStatus.STARTED, GatewaySidecarStatus.REUSED}
        and lease.readiness_confirmed
    )


def _validate_session_id(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 256
        or any(character in value for character in ("\r", "\n", "\x00"))
    ):
        raise GatewayRouteStartError("gateway_session_binding_invalid")


def _safe_reason_id(error: ValueError) -> str:
    value = str(error)
    if (
        not value
        or len(value) > 128
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789_"
            for character in value
        )
    ):
        return "gateway_startup_environment_invalid"
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("gateway route clock must be timezone-aware")
    return value


def _credential_free_base_url(parsed: Any) -> str:
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


__all__ = ["GatewayRouteStartError", "GatewayRouteWebService"]
