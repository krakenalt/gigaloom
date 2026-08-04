"""Bounded discovery of reviewed routes from public gateway HTTP contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
import re
from typing import Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import HTTPRedirectHandler, Request, build_opener

from gigaloom.contracts.operational_validation import canonical_digest
from gigaloom.native.launch.gateway_codec import bridge_route_to_dict
from gigaloom.native.launch.gateway_contracts import (
    BridgeRouteV1,
    GatewayProfileV1,
    GatewaySupportStatus,
)


MAX_GATEWAY_DISCOVERY_BYTES = 1_048_576
MAX_GATEWAY_DISCOVERY_MODELS = 512
MAX_GATEWAY_DISCOVERY_CELLS = 512
DEFAULT_GATEWAY_DISCOVERY_TTL_SECONDS = 60
MAX_GATEWAY_API_KEY_CHARS = 4096
_PROTOCOL_AGENTS = {
    "openai_responses": "codex",
    "anthropic_messages": "claude",
    "acp": "acp",
}
_PROVIDER_ALIASES = {
    "sber": "gigachat",
    "sberbank": "gigachat",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class GatewayDiscoveryStatus(str, Enum):
    """Freshness state for one route-catalog projection."""

    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class GatewayDiscoveryReason(str, Enum):
    """Content-free machine-discovery failure reasons."""

    HEALTH_UNAVAILABLE = "health_unavailable"
    MODELS_UNAVAILABLE = "models_unavailable"
    CAPABILITIES_UNAVAILABLE = "capabilities_unavailable"
    CONTRACT_INVALID = "contract_invalid"
    CONTRACT_REVISION_MISMATCH = "contract_revision_mismatch"


class GatewayDiscoveryError(RuntimeError):
    """Typed content-free machine-contract discovery failure."""

    def __init__(self, reason: GatewayDiscoveryReason):
        super().__init__(reason.value)
        self.reason = reason


class GatewayMachineTransport(Protocol):
    """GET-only public machine-contract transport."""

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> tuple[int, object]: ...


@dataclass(frozen=True)
class GatewayRouteCatalogV1:
    """Current content-free route facts from one exact gateway profile."""

    gateway_id: str
    profile_digest: str
    models_revision: str
    capabilities_revision: str
    loss_matrix_revision: str
    routes: tuple[BridgeRouteV1, ...]
    discovered_at: str
    expires_at: str
    catalog_digest: str


@dataclass(frozen=True)
class GatewayDiscoveryResult:
    """Fresh, stale, or unknown result without fallback authority."""

    status: GatewayDiscoveryStatus
    catalog: GatewayRouteCatalogV1 | None
    reason_ids: tuple[GatewayDiscoveryReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, GatewayDiscoveryStatus):
            raise ValueError("gateway discovery status is invalid")
        if self.status is GatewayDiscoveryStatus.UNKNOWN:
            if self.catalog is not None or not self.reason_ids:
                raise ValueError("unknown gateway discovery requires only reasons")
        elif self.catalog is None:
            raise ValueError("known gateway discovery requires a catalog")
        if self.status is GatewayDiscoveryStatus.CURRENT and self.reason_ids:
            raise ValueError("current gateway discovery cannot contain failure reasons")


class UrlLibGatewayMachineTransport:
    """Bounded JSON GET transport with redirects disabled."""

    def __init__(self, api_key: str | None = None) -> None:
        if api_key is not None and (
            len(api_key) > MAX_GATEWAY_API_KEY_CHARS
            or any(character in api_key for character in ("\r", "\n", "\x00"))
        ):
            raise ValueError("gateway API key is invalid")
        self._api_key = api_key

    def get_json(
        self,
        base_url: str,
        path: str,
        *,
        timeout_seconds: float,
    ) -> tuple[int, object]:
        if path not in {"/health", "/models", "/bridge/capabilities"}:
            raise ValueError("gateway discovery path is not admitted")
        headers = {"accept": "application/json"}
        if self._api_key and self._api_key != "0":
            headers.update(
                {
                    "authorization": f"Bearer {self._api_key}",
                    "x-api-key": self._api_key,
                }
            )
        request = Request(
            urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
            headers=headers,
            method="GET",
        )
        opener = build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                payload = _read_bounded_json(response)
                return int(response.status), payload
        except HTTPError as error:
            _drain_bounded(error)
            return int(error.code), None
        except (OSError, TimeoutError, URLError) as error:
            raise GatewayDiscoveryError(
                GatewayDiscoveryReason.HEALTH_UNAVAILABLE
            ) from error


class GatewayRouteDiscovery:
    """Discover and cache route facts without invoking inference endpoints."""

    def __init__(
        self,
        transport: GatewayMachineTransport | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        ttl_seconds: int = DEFAULT_GATEWAY_DISCOVERY_TTL_SECONDS,
        timeout_seconds: float = 3.0,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("gateway discovery TTL must be positive")
        if timeout_seconds <= 0:
            raise ValueError("gateway discovery timeout must be positive")
        self._transport = transport or UrlLibGatewayMachineTransport()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._cache: dict[tuple[str, str], GatewayRouteCatalogV1] = {}

    def discover(
        self,
        profile: GatewayProfileV1,
        *,
        force_refresh: bool = False,
    ) -> GatewayDiscoveryResult:
        """Return current facts or an explicitly stale/unknown result."""
        now = _aware(self._clock())
        cache_key = (profile.gateway_id, profile.profile_digest)
        cached = self._cache.get(cache_key)
        if (
            not force_refresh
            and cached is not None
            and _parse_time(cached.expires_at) > now
        ):
            return GatewayDiscoveryResult(GatewayDiscoveryStatus.CURRENT, cached)
        try:
            catalog = self._fetch(profile, now=now)
        except GatewayDiscoveryError as error:
            if cached is None:
                return GatewayDiscoveryResult(
                    GatewayDiscoveryStatus.UNKNOWN,
                    None,
                    (error.reason,),
                )
            return GatewayDiscoveryResult(
                GatewayDiscoveryStatus.STALE,
                cached,
                (error.reason,),
            )
        self._cache[cache_key] = catalog
        return GatewayDiscoveryResult(GatewayDiscoveryStatus.CURRENT, catalog)

    def invalidate(self, gateway_id: str) -> None:
        """Drop only cached projections for one gateway identity."""
        for key in tuple(self._cache):
            if key[0] == gateway_id:
                del self._cache[key]

    def _fetch(
        self,
        profile: GatewayProfileV1,
        *,
        now: datetime,
    ) -> GatewayRouteCatalogV1:
        health_status, _ = self._get(profile, "/health")
        if health_status != 200:
            raise GatewayDiscoveryError(GatewayDiscoveryReason.HEALTH_UNAVAILABLE)
        models_status, models_payload = self._get(profile, "/models")
        if models_status != 200:
            raise GatewayDiscoveryError(GatewayDiscoveryReason.MODELS_UNAVAILABLE)
        capabilities_status, capabilities_payload = self._get(
            profile,
            "/bridge/capabilities",
        )
        if capabilities_status != 200:
            raise GatewayDiscoveryError(GatewayDiscoveryReason.CAPABILITIES_UNAVAILABLE)
        try:
            models = _parse_models(models_payload)
            matrix_revision, cells = _parse_capabilities(
                capabilities_payload,
                expected_schema=profile.capabilities_contract_revision,
            )
            routes = _build_routes(
                gateway_id=profile.gateway_id,
                models=models,
                matrix_revision=matrix_revision,
                cells=cells,
            )
        except GatewayDiscoveryError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            raise GatewayDiscoveryError(
                GatewayDiscoveryReason.CONTRACT_INVALID
            ) from error
        models_revision = "sha256:" + canonical_digest(models_payload)
        capabilities_revision = _capabilities_revision(
            capabilities_payload,
            matrix_revision,
        )
        discovered_at = now.isoformat()
        expires_at = (now + timedelta(seconds=self._ttl_seconds)).isoformat()
        semantic = {
            "gateway_id": profile.gateway_id,
            "profile_digest": profile.profile_digest,
            "models_revision": models_revision,
            "capabilities_revision": capabilities_revision,
            "loss_matrix_revision": matrix_revision,
            "routes": [bridge_route_to_dict(route) for route in routes],
            "discovered_at": discovered_at,
            "expires_at": expires_at,
        }
        return GatewayRouteCatalogV1(
            gateway_id=profile.gateway_id,
            profile_digest=profile.profile_digest,
            models_revision=models_revision,
            capabilities_revision=capabilities_revision,
            loss_matrix_revision=matrix_revision,
            routes=routes,
            discovered_at=discovered_at,
            expires_at=expires_at,
            catalog_digest=canonical_digest(semantic),
        )

    def _get(
        self,
        profile: GatewayProfileV1,
        path: str,
    ) -> tuple[int, object]:
        try:
            return self._transport.get_json(
                profile.base_url,
                path,
                timeout_seconds=self._timeout_seconds,
            )
        except GatewayDiscoveryError:
            raise
        except Exception as error:
            reason = {
                "/health": GatewayDiscoveryReason.HEALTH_UNAVAILABLE,
                "/models": GatewayDiscoveryReason.MODELS_UNAVAILABLE,
                "/bridge/capabilities": (
                    GatewayDiscoveryReason.CAPABILITIES_UNAVAILABLE
                ),
            }[path]
            raise GatewayDiscoveryError(reason) from error


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


def _read_bounded_json(response: Any) -> object:
    data = response.read(MAX_GATEWAY_DISCOVERY_BYTES + 1)
    if len(data) > MAX_GATEWAY_DISCOVERY_BYTES:
        raise GatewayDiscoveryError(GatewayDiscoveryReason.CONTRACT_INVALID)
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GatewayDiscoveryError(GatewayDiscoveryReason.CONTRACT_INVALID) from error


def _drain_bounded(response: Any) -> None:
    try:
        response.read(MAX_GATEWAY_DISCOVERY_BYTES + 1)
    except OSError:
        return


def _parse_models(payload: object) -> tuple[Mapping[str, str], ...]:
    if not isinstance(payload, Mapping):
        raise ValueError("models contract must be an object list")
    document = cast(Mapping[str, object], payload)
    if document.get("object") != "list":
        raise ValueError("models contract must be an object list")
    data = document.get("data")
    if not isinstance(data, list) or len(data) > MAX_GATEWAY_DISCOVERY_MODELS:
        raise ValueError("models contract is not bounded")
    models: list[Mapping[str, str]] = []
    for raw in data:
        if not isinstance(raw, Mapping):
            raise ValueError("model entry must be an object")
        model_document = cast(Mapping[str, object], raw)
        model_id = _required_text(model_document.get("id"), "model id")
        owner = _optional_text(
            model_document.get("upstream_provider")
        ) or _optional_text(model_document.get("owned_by"))
        upstream_model = (
            _optional_text(model_document.get("upstream_model")) or model_id
        )
        model: dict[str, str] = {"id": model_id, "upstream_model": upstream_model}
        if owner is not None:
            model["upstream_provider"] = _normalize_provider(owner)
        models.append(model)
    if len({item["id"] for item in models}) != len(models):
        raise ValueError("model ids must be unique")
    return tuple(sorted(models, key=lambda item: item["id"]))


def _parse_capabilities(
    payload: object,
    *,
    expected_schema: str,
) -> tuple[str, tuple[Mapping[str, object], ...]]:
    if not isinstance(payload, Mapping):
        raise ValueError("capabilities contract must be an object")
    document = cast(Mapping[str, object], payload)
    if document.get("schema_version") != expected_schema:
        raise GatewayDiscoveryError(GatewayDiscoveryReason.CONTRACT_REVISION_MISMATCH)
    matrix_revision = _required_text(
        document.get("matrix_revision"),
        "matrix revision",
    )
    cells = document.get("cells")
    if not isinstance(cells, list) or len(cells) > MAX_GATEWAY_DISCOVERY_CELLS:
        raise ValueError("capability cells are not bounded")
    if not all(isinstance(cell, Mapping) for cell in cells):
        raise ValueError("capability cells must be objects")
    return matrix_revision, cast(tuple[Mapping[str, object], ...], tuple(cells))


def _build_routes(
    *,
    gateway_id: str,
    models: tuple[Mapping[str, str], ...],
    matrix_revision: str,
    cells: tuple[Mapping[str, object], ...],
) -> tuple[BridgeRouteV1, ...]:
    admitted_cells: list[
        tuple[str, str, GatewaySupportStatus, Mapping[str, object]]
    ] = []
    for cell in cells:
        protocol = _required_text(cell.get("public_protocol"), "public protocol")
        agent_id = _PROTOCOL_AGENTS.get(protocol)
        if agent_id is None:
            continue
        provider = _normalize_provider(
            _required_text(cell.get("upstream_provider"), "upstream provider")
        )
        support = GatewaySupportStatus(
            _required_text(cell.get("status"), "support status")
        )
        admitted_cells.append((agent_id, provider, support, cell))
    active_providers = {
        provider
        for _, provider, support, _ in admitted_cells
        if support is not GatewaySupportStatus.BLOCKED
    }
    inferred_provider = (
        next(iter(active_providers)) if len(active_providers) == 1 else None
    )
    routes: list[BridgeRouteV1] = []
    for model in models:
        provider = model.get("upstream_provider") or inferred_provider
        if provider is None:
            continue
        for agent_id, cell_provider, support, cell in admitted_cells:
            if cell_provider != provider:
                continue
            protocol = _required_text(cell.get("public_protocol"), "public protocol")
            alias = model["id"]
            routes.append(
                BridgeRouteV1(
                    route_id="-".join(
                        (_slug(agent_id), _slug(gateway_id), _slug(alias))
                    ),
                    agent_id=agent_id,
                    client_protocol=protocol,
                    gateway_profile_id=gateway_id,
                    public_model_alias=alias,
                    upstream_provider=provider,
                    upstream_model=model["upstream_model"],
                    capability_profile_revision=_capability_revision_for_cell(
                        cell,
                        matrix_revision,
                    ),
                    loss_matrix_revision=matrix_revision,
                    support_status=support,
                    reason_ids=_string_tuple(cell.get("reason_ids"), "reason ids"),
                    evidence_ids=_string_tuple(
                        cell.get("evidence_ids"),
                        "evidence ids",
                    ),
                    required_acknowledgement=(
                        "acknowledge_vendor_unsupported"
                        if support is GatewaySupportStatus.VENDOR_UNSUPPORTED
                        else None
                    ),
                )
            )
    unique = {route.route_id: route for route in routes}
    if len(unique) != len(routes):
        raise ValueError("gateway route ids are not unique")
    return tuple(unique[key] for key in sorted(unique))


def _capabilities_revision(payload: object, fallback: str) -> str:
    if isinstance(payload, Mapping):
        document = cast(Mapping[str, object], payload)
        revision = _optional_text(document.get("capability_revision"))
        if revision is not None:
            return revision
    return fallback


def _capability_revision_for_cell(
    cell: Mapping[str, object],
    fallback: str,
) -> str:
    return _optional_text(cell.get("capability_revision")) or fallback


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string list")
    return cast(tuple[str, ...], tuple(value))


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _normalize_provider(value: str) -> str:
    lowered = _slug(value)
    return _PROVIDER_ALIASES.get(lowered, lowered)


def _slug(value: str) -> str:
    normalized = _SLUG_RE.sub("-", value.casefold()).strip("-")
    if not normalized:
        raise ValueError("gateway identity cannot be slugged")
    return normalized


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("gateway discovery clock must be timezone-aware")
    return value


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
