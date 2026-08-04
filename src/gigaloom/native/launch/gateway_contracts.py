"""Immutable contracts for reviewed gateway-backed native launches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
from urllib.parse import urlsplit


GATEWAY_LAUNCH_SCHEMA_VERSION = 1
MAX_GATEWAY_ITEMS = 128
MAX_GATEWAY_TEXT_CHARS = 4096
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_ENV_NAME_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
_SECRET_ENV_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class GatewayMode(str, Enum):
    """Supported gateway process topologies."""

    MANAGED = "managed"
    EXTERNAL = "external"


class GatewaySupportStatus(str, Enum):
    """Honest route-support vocabulary frozen by the 0.9 ADR."""

    STABLE = "stable"
    TECHNICAL_PREVIEW = "technical_preview"
    VENDOR_UNSUPPORTED = "vendor_unsupported"
    BLOCKED = "blocked"


class GatewayPreflightStatus(str, Enum):
    """Execution-free readiness result for one exact route binding."""

    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ResolvedGatewayRoute:
    """Credential-free route facts shared by every launch consumer."""

    route_id: str
    gateway_id: str
    provider_protocol: str
    credential_free_base_url: str
    public_model_alias: str
    support_status: str
    capability_digest: str
    reason_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.route_id, "resolved route id"),
            (self.gateway_id, "resolved route gateway id"),
            (self.provider_protocol, "resolved route provider protocol"),
            (self.public_model_alias, "resolved route public model alias"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_http_url(self.credential_free_base_url)
        if self.support_status not in {status.value for status in GatewaySupportStatus}:
            raise ValueError("resolved route support status is invalid")
        _validate_digest(
            self.capability_digest,
            field_name="resolved route capability digest",
        )
        _validate_identity_tuple(
            self.reason_ids,
            field_name="resolved route reason ids",
        )


@dataclass(frozen=True)
class GatewayProfileV1:
    """One exact public gateway artifact and machine-contract profile."""

    gateway_id: str
    display_name: str
    mode: GatewayMode
    distribution: str
    executable: str
    version: str
    version_window: str
    artifact_sha256: str
    base_url: str
    startup_config_revision: str
    health_contract_revision: str
    readiness_contract_revision: str
    models_contract_revision: str
    capabilities_contract_revision: str
    auth_ref: str | None
    tls_policy_ref: str | None
    profile_digest: str
    schema_version: int = GATEWAY_LAUNCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        for value, field_name in (
            (self.gateway_id, "gateway id"),
            (self.distribution, "gateway distribution"),
            (self.executable, "gateway executable"),
            (self.version, "gateway version"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_text(self.display_name, field_name="gateway display name")
        if not isinstance(self.mode, GatewayMode):
            raise ValueError("gateway mode is invalid")
        _validate_text(self.version_window, field_name="gateway version window")
        _validate_digest(self.artifact_sha256, field_name="gateway artifact sha256")
        _validate_http_url(self.base_url)
        for value, field_name in (
            (self.startup_config_revision, "startup config revision"),
            (self.health_contract_revision, "health contract revision"),
            (self.readiness_contract_revision, "readiness contract revision"),
            (self.models_contract_revision, "models contract revision"),
            (self.capabilities_contract_revision, "capabilities contract revision"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_optional_ref(self.auth_ref, field_name="gateway auth ref")
        _validate_optional_ref(self.tls_policy_ref, field_name="TLS policy ref")
        _validate_digest(self.profile_digest, field_name="gateway profile digest")


@dataclass(frozen=True)
class BridgeRouteV1:
    """Immutable agent, protocol, gateway, model, and support binding."""

    route_id: str
    agent_id: str
    client_protocol: str
    gateway_profile_id: str
    public_model_alias: str
    upstream_provider: str
    upstream_model: str
    capability_profile_revision: str
    loss_matrix_revision: str
    support_status: GatewaySupportStatus
    reason_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    reasoning_selector: str | None = None
    required_acknowledgement: str | None = None
    schema_version: int = GATEWAY_LAUNCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        for value, field_name in (
            (self.route_id, "bridge route id"),
            (self.agent_id, "bridge route agent id"),
            (self.client_protocol, "bridge route client protocol"),
            (self.gateway_profile_id, "bridge route gateway profile id"),
            (self.public_model_alias, "bridge route public model alias"),
            (self.upstream_provider, "bridge route upstream provider"),
            (self.upstream_model, "bridge route upstream model"),
            (self.capability_profile_revision, "capability profile revision"),
            (self.loss_matrix_revision, "loss matrix revision"),
        ):
            _validate_identity(value, field_name=field_name)
        if not isinstance(self.support_status, GatewaySupportStatus):
            raise ValueError("gateway support status is invalid")
        _validate_identity_tuple(self.reason_ids, field_name="route reason ids")
        _validate_identity_tuple(self.evidence_ids, field_name="route evidence ids")
        _validate_optional_identity(
            self.reasoning_selector,
            field_name="route reasoning selector",
        )
        _validate_optional_identity(
            self.required_acknowledgement,
            field_name="route required acknowledgement",
        )
        if (
            self.support_status is GatewaySupportStatus.VENDOR_UNSUPPORTED
            and self.required_acknowledgement is None
        ):
            raise ValueError("vendor-unsupported route requires acknowledgement")
        if self.support_status is GatewaySupportStatus.BLOCKED and not self.reason_ids:
            raise ValueError("blocked route requires a reason id")


@dataclass(frozen=True)
class LaunchOverlayV1:
    """Redaction-safe generated state consumed by one native launch."""

    route_id: str
    managed_home: str
    redacted_env_delta: tuple[tuple[str, str], ...]
    generated_config_refs: tuple[str, ...]
    process_lease_ref: str | None
    preflight_receipt_ref: str
    gateway_capability_digest: str
    schema_version: int = GATEWAY_LAUNCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        _validate_identity(self.route_id, field_name="launch overlay route id")
        _validate_managed_path(self.managed_home, field_name="managed home")
        _validate_env_delta(self.redacted_env_delta)
        _validate_ref_tuple(
            self.generated_config_refs,
            field_name="generated config refs",
        )
        _validate_optional_ref(
            self.process_lease_ref,
            field_name="process lease ref",
        )
        _validate_ref(
            self.preflight_receipt_ref,
            field_name="preflight receipt ref",
        )
        _validate_digest(
            self.gateway_capability_digest,
            field_name="gateway capability digest",
        )


@dataclass(frozen=True)
class GatewayPreflightReceiptV1:
    """Content-free receipt for route admission against current gateway facts."""

    receipt_id: str
    gateway_id: str
    route_id: str
    profile_digest: str
    artifact_sha256: str
    capability_revision: str
    models_revision: str
    loss_matrix_revision: str
    support_status: GatewaySupportStatus
    status: GatewayPreflightStatus
    reason_ids: tuple[str, ...]
    checked_at: str
    schema_version: int = GATEWAY_LAUNCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_schema(self.schema_version)
        for value, field_name in (
            (self.receipt_id, "gateway receipt id"),
            (self.gateway_id, "gateway receipt gateway id"),
            (self.route_id, "gateway receipt route id"),
            (self.capability_revision, "gateway receipt capability revision"),
            (self.models_revision, "gateway receipt models revision"),
            (self.loss_matrix_revision, "gateway receipt loss matrix revision"),
        ):
            _validate_identity(value, field_name=field_name)
        _validate_digest(self.profile_digest, field_name="receipt profile digest")
        _validate_digest(self.artifact_sha256, field_name="receipt artifact sha256")
        if not isinstance(self.support_status, GatewaySupportStatus):
            raise ValueError("receipt support status is invalid")
        if not isinstance(self.status, GatewayPreflightStatus):
            raise ValueError("gateway preflight status is invalid")
        _validate_identity_tuple(self.reason_ids, field_name="receipt reason ids")
        _validate_timestamp(self.checked_at, field_name="receipt checked_at")
        if self.status is GatewayPreflightStatus.BLOCKED and not self.reason_ids:
            raise ValueError("blocked gateway preflight requires a reason id")
        if (
            self.status is GatewayPreflightStatus.READY
            and self.support_status is GatewaySupportStatus.BLOCKED
        ):
            raise ValueError("blocked route cannot have a ready preflight")


def _validate_schema(value: object) -> None:
    if value != GATEWAY_LAUNCH_SCHEMA_VERSION:
        raise ValueError("unsupported gateway launch schema_version")


def _validate_identity(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _validate_optional_identity(value: object, *, field_name: str) -> None:
    if value is not None:
        _validate_identity(value, field_name=field_name)


def _validate_text(value: object, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > MAX_GATEWAY_TEXT_CHARS
    ):
        raise ValueError(f"{field_name} is invalid")


def _validate_digest(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")


def _validate_http_url(value: object) -> None:
    _validate_text(value, field_name="gateway base URL")
    assert isinstance(value, str)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("gateway base URL must be credential-free HTTP(S)")


def _validate_identity_tuple(value: object, *, field_name: str) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_GATEWAY_ITEMS:
        raise ValueError(f"{field_name} must be a bounded tuple")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must be unique")
    for item in value:
        _validate_identity(item, field_name=field_name)


def _validate_ref(value: object, *, field_name: str) -> None:
    _validate_text(value, field_name=field_name)
    assert isinstance(value, str)
    if ":" not in value:
        raise ValueError(f"{field_name} must be namespaced")


def _validate_optional_ref(value: object, *, field_name: str) -> None:
    if value is not None:
        _validate_ref(value, field_name=field_name)


def _validate_ref_tuple(value: object, *, field_name: str) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_GATEWAY_ITEMS:
        raise ValueError(f"{field_name} must be a bounded tuple")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must be unique")
    for item in value:
        _validate_ref(item, field_name=field_name)


def _validate_managed_path(value: object, *, field_name: str) -> None:
    _validate_text(value, field_name=field_name)
    assert isinstance(value, str)
    if not value.startswith(("/", "managed:")):
        raise ValueError(f"{field_name} must be an absolute or managed reference")


def _validate_env_delta(value: object) -> None:
    if not isinstance(value, tuple) or len(value) > MAX_GATEWAY_ITEMS:
        raise ValueError("redacted env delta must be a bounded tuple")
    names: list[str] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError("redacted env delta entries must be name/value tuples")
        name, projected = item
        if not isinstance(name, str) or _ENV_NAME_RE.fullmatch(name) is None:
            raise ValueError("redacted env delta contains an invalid name")
        _validate_text(projected, field_name="redacted env value")
        if any(part in name for part in _SECRET_ENV_PARTS) and projected not in {
            "<redacted>",
            "<secret-ref>",
        }:
            raise ValueError("secret-like environment values must be redacted")
        names.append(name)
    if names != sorted(set(names)):
        raise ValueError("redacted env delta names must be sorted and unique")


def _validate_timestamp(value: object, *, field_name: str) -> None:
    _validate_text(value, field_name=field_name)
    assert isinstance(value, str)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
