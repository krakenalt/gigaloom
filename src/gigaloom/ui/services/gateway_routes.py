"""Reviewed gateway route catalog and submission binding for Web."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    UrlLibGatewayMachineTransport,
)
from gigaloom.native.launch.gateway_profile import (
    resolve_installed_gpt2giga_artifact,
    reviewed_gpt2giga_profile,
)
from gigaloom.native.launch.gateway_sidecar import GatewayArtifactEvidenceV1


_PREFLIGHT_TTL = timedelta(minutes=5)
ArtifactResolver = Callable[[GatewayProfileV1], GatewayArtifactEvidenceV1 | None]


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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.profile = profile
        self.discovery = discovery
        self.artifact_resolver = artifact_resolver
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._issued: dict[str, _IssuedPreflight] = {}

    @classmethod
    def from_config(cls, config: HarnessConfig) -> GatewayRouteWebService:
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
        return cls(
            profile,
            GatewayRouteDiscovery(
                UrlLibGatewayMachineTransport(api_key=config.api_key)
            ),
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
        }

    def preflight(
        self,
        route_id: str,
        *,
        acknowledgement_id: str | None,
    ) -> dict[str, Any]:
        """Issue one short-lived receipt for the exact current route facts."""
        result = self.discovery.discover(self.profile, force_refresh=True)
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
        if self.profile.mode is GatewayMode.MANAGED and not _artifact_matches(
            self.profile, artifact
        ):
            raise ValueError("managed gateway artifact is not verified")
        now = _aware(self.clock())
        binding = {
            "gateway_id": self.profile.gateway_id,
            "route_id": route.route_id,
            "profile_digest": self.profile.profile_digest,
            "artifact_sha256": self.profile.artifact_sha256,
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
            artifact_sha256=self.profile.artifact_sha256,
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
        result = self.discovery.discover(self.profile, force_refresh=True)
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
            "artifact_sha256": self.profile.artifact_sha256,
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


def _artifact_matches(
    profile: GatewayProfileV1,
    artifact: GatewayArtifactEvidenceV1 | None,
) -> bool:
    return bool(
        artifact is not None
        and artifact.verified
        and artifact.distribution == profile.distribution
        and artifact.version == profile.version
        and artifact.artifact_sha256 == profile.artifact_sha256
        and artifact.executable_path
    )


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


__all__ = ["GatewayRouteWebService"]
