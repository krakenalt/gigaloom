"""Strict canonical serialization for gateway launch contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from gigaloom.contracts.operational_validation import canonical_digest, require_mapping
from gigaloom.native.launch.gateway_contracts import (
    BridgeRouteV1,
    GatewayMode,
    GatewayPreflightReceiptV1,
    GatewayPreflightStatus,
    GatewayProfileV1,
    GatewaySupportStatus,
    LaunchOverlayV1,
)


_EnumT = TypeVar("_EnumT")


def gateway_profile_to_dict(value: GatewayProfileV1) -> dict[str, Any]:
    """Serialize one exact gateway profile."""
    return {
        "schema_version": value.schema_version,
        "gateway_id": value.gateway_id,
        "display_name": value.display_name,
        "mode": value.mode.value,
        "distribution": value.distribution,
        "executable": value.executable,
        "version": value.version,
        "version_window": value.version_window,
        "artifact_sha256": value.artifact_sha256,
        "base_url": value.base_url,
        "startup_config_revision": value.startup_config_revision,
        "health_contract_revision": value.health_contract_revision,
        "readiness_contract_revision": value.readiness_contract_revision,
        "models_contract_revision": value.models_contract_revision,
        "capabilities_contract_revision": value.capabilities_contract_revision,
        "auth_ref": value.auth_ref,
        "tls_policy_ref": value.tls_policy_ref,
        "profile_digest": value.profile_digest,
    }


def gateway_profile_from_dict(payload: Mapping[str, Any]) -> GatewayProfileV1:
    """Decode one strict gateway profile."""
    fields = _strict(payload, gateway_profile_to_dict(_profile_example()), "profile")
    return GatewayProfileV1(
        schema_version=_integer(fields["schema_version"], "schema_version"),
        gateway_id=_string(fields["gateway_id"], "gateway_id"),
        display_name=_string(fields["display_name"], "display_name"),
        mode=_enum(GatewayMode, fields["mode"], "mode"),
        distribution=_string(fields["distribution"], "distribution"),
        executable=_string(fields["executable"], "executable"),
        version=_string(fields["version"], "version"),
        version_window=_string(fields["version_window"], "version_window"),
        artifact_sha256=_string(fields["artifact_sha256"], "artifact_sha256"),
        base_url=_string(fields["base_url"], "base_url"),
        startup_config_revision=_string(
            fields["startup_config_revision"], "startup_config_revision"
        ),
        health_contract_revision=_string(
            fields["health_contract_revision"], "health_contract_revision"
        ),
        readiness_contract_revision=_string(
            fields["readiness_contract_revision"], "readiness_contract_revision"
        ),
        models_contract_revision=_string(
            fields["models_contract_revision"], "models_contract_revision"
        ),
        capabilities_contract_revision=_string(
            fields["capabilities_contract_revision"],
            "capabilities_contract_revision",
        ),
        auth_ref=_optional_string(fields["auth_ref"], "auth_ref"),
        tls_policy_ref=_optional_string(fields["tls_policy_ref"], "tls_policy_ref"),
        profile_digest=_string(fields["profile_digest"], "profile_digest"),
    )


def bridge_route_to_dict(value: BridgeRouteV1) -> dict[str, Any]:
    """Serialize one immutable reviewed bridge route."""
    return {
        "schema_version": value.schema_version,
        "route_id": value.route_id,
        "agent_id": value.agent_id,
        "client_protocol": value.client_protocol,
        "gateway_profile_id": value.gateway_profile_id,
        "public_model_alias": value.public_model_alias,
        "upstream_provider": value.upstream_provider,
        "upstream_model": value.upstream_model,
        "capability_profile_revision": value.capability_profile_revision,
        "loss_matrix_revision": value.loss_matrix_revision,
        "support_status": value.support_status.value,
        "reason_ids": list(value.reason_ids),
        "evidence_ids": list(value.evidence_ids),
        "reasoning_selector": value.reasoning_selector,
        "required_acknowledgement": value.required_acknowledgement,
    }


def bridge_route_from_dict(payload: Mapping[str, Any]) -> BridgeRouteV1:
    """Decode one strict bridge route."""
    fields = _strict(payload, bridge_route_to_dict(_route_example()), "route")
    return BridgeRouteV1(
        schema_version=_integer(fields["schema_version"], "schema_version"),
        route_id=_string(fields["route_id"], "route_id"),
        agent_id=_string(fields["agent_id"], "agent_id"),
        client_protocol=_string(fields["client_protocol"], "client_protocol"),
        gateway_profile_id=_string(fields["gateway_profile_id"], "gateway_profile_id"),
        public_model_alias=_string(fields["public_model_alias"], "public_model_alias"),
        upstream_provider=_string(fields["upstream_provider"], "upstream_provider"),
        upstream_model=_string(fields["upstream_model"], "upstream_model"),
        capability_profile_revision=_string(
            fields["capability_profile_revision"], "capability_profile_revision"
        ),
        loss_matrix_revision=_string(
            fields["loss_matrix_revision"], "loss_matrix_revision"
        ),
        support_status=_enum(
            GatewaySupportStatus,
            fields["support_status"],
            "support_status",
        ),
        reason_ids=_string_tuple(fields["reason_ids"], "reason_ids"),
        evidence_ids=_string_tuple(fields["evidence_ids"], "evidence_ids"),
        reasoning_selector=_optional_string(
            fields["reasoning_selector"], "reasoning_selector"
        ),
        required_acknowledgement=_optional_string(
            fields["required_acknowledgement"], "required_acknowledgement"
        ),
    )


def launch_overlay_to_dict(value: LaunchOverlayV1) -> dict[str, Any]:
    """Serialize a redaction-safe launch overlay."""
    return {
        "schema_version": value.schema_version,
        "route_id": value.route_id,
        "managed_home": value.managed_home,
        "redacted_env_delta": {
            name: projected for name, projected in value.redacted_env_delta
        },
        "generated_config_refs": list(value.generated_config_refs),
        "process_lease_ref": value.process_lease_ref,
        "preflight_receipt_ref": value.preflight_receipt_ref,
        "gateway_capability_digest": value.gateway_capability_digest,
    }


def launch_overlay_from_dict(payload: Mapping[str, Any]) -> LaunchOverlayV1:
    """Decode one strict redaction-safe launch overlay."""
    fields = _strict(payload, launch_overlay_to_dict(_overlay_example()), "overlay")
    env = fields["redacted_env_delta"]
    if not isinstance(env, Mapping) or any(not isinstance(key, str) for key in env):
        raise ValueError("redacted_env_delta must be an object")
    return LaunchOverlayV1(
        schema_version=_integer(fields["schema_version"], "schema_version"),
        route_id=_string(fields["route_id"], "route_id"),
        managed_home=_string(fields["managed_home"], "managed_home"),
        redacted_env_delta=tuple(
            sorted(
                (
                    key,
                    _string(projected, f"redacted_env_delta.{key}"),
                )
                for key, projected in env.items()
            )
        ),
        generated_config_refs=_string_tuple(
            fields["generated_config_refs"], "generated_config_refs"
        ),
        process_lease_ref=_optional_string(
            fields["process_lease_ref"], "process_lease_ref"
        ),
        preflight_receipt_ref=_string(
            fields["preflight_receipt_ref"], "preflight_receipt_ref"
        ),
        gateway_capability_digest=_string(
            fields["gateway_capability_digest"], "gateway_capability_digest"
        ),
    )


def gateway_preflight_receipt_to_dict(
    value: GatewayPreflightReceiptV1,
) -> dict[str, Any]:
    """Serialize a content-free gateway preflight receipt."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "gateway_id": value.gateway_id,
        "route_id": value.route_id,
        "profile_digest": value.profile_digest,
        "artifact_sha256": value.artifact_sha256,
        "capability_revision": value.capability_revision,
        "models_revision": value.models_revision,
        "loss_matrix_revision": value.loss_matrix_revision,
        "support_status": value.support_status.value,
        "status": value.status.value,
        "reason_ids": list(value.reason_ids),
        "checked_at": value.checked_at,
    }


def gateway_preflight_receipt_from_dict(
    payload: Mapping[str, Any],
) -> GatewayPreflightReceiptV1:
    """Decode one strict gateway preflight receipt."""
    fields = _strict(
        payload,
        gateway_preflight_receipt_to_dict(_receipt_example()),
        "preflight receipt",
    )
    return GatewayPreflightReceiptV1(
        schema_version=_integer(fields["schema_version"], "schema_version"),
        receipt_id=_string(fields["receipt_id"], "receipt_id"),
        gateway_id=_string(fields["gateway_id"], "gateway_id"),
        route_id=_string(fields["route_id"], "route_id"),
        profile_digest=_string(fields["profile_digest"], "profile_digest"),
        artifact_sha256=_string(fields["artifact_sha256"], "artifact_sha256"),
        capability_revision=_string(
            fields["capability_revision"], "capability_revision"
        ),
        models_revision=_string(fields["models_revision"], "models_revision"),
        loss_matrix_revision=_string(
            fields["loss_matrix_revision"], "loss_matrix_revision"
        ),
        support_status=_enum(
            GatewaySupportStatus,
            fields["support_status"],
            "support_status",
        ),
        status=_enum(GatewayPreflightStatus, fields["status"], "status"),
        reason_ids=_string_tuple(fields["reason_ids"], "reason_ids"),
        checked_at=_string(fields["checked_at"], "checked_at"),
    )


def gateway_contract_digest(value: object) -> str:
    """Return the canonical digest for one supported gateway contract."""
    if isinstance(value, GatewayProfileV1):
        payload = gateway_profile_to_dict(value)
    elif isinstance(value, BridgeRouteV1):
        payload = bridge_route_to_dict(value)
    elif isinstance(value, LaunchOverlayV1):
        payload = launch_overlay_to_dict(value)
    elif isinstance(value, GatewayPreflightReceiptV1):
        payload = gateway_preflight_receipt_to_dict(value)
    else:
        raise TypeError("unsupported gateway contract type")
    return canonical_digest(payload)


def _strict(
    payload: Mapping[str, Any],
    example: Mapping[str, Any],
    field_name: str,
) -> Mapping[str, Any]:
    return require_mapping(payload, required=set(example), field_name=field_name)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string list")
    return cast(tuple[str, ...], tuple(value))


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _profile_example() -> GatewayProfileV1:
    return GatewayProfileV1(
        gateway_id="example",
        display_name="Example",
        mode=GatewayMode.EXTERNAL,
        distribution="example",
        executable="example",
        version="1.0.0",
        version_window=">=1,<2",
        artifact_sha256="0" * 64,
        base_url="http://127.0.0.1:1",
        startup_config_revision="example.startup.v1",
        health_contract_revision="example.health.v1",
        readiness_contract_revision="example.readiness.v1",
        models_contract_revision="example.models.v1",
        capabilities_contract_revision="example.capabilities.v1",
        auth_ref=None,
        tls_policy_ref=None,
        profile_digest="1" * 64,
    )


def _route_example() -> BridgeRouteV1:
    return BridgeRouteV1(
        route_id="example-route",
        agent_id="example-agent",
        client_protocol="example_protocol",
        gateway_profile_id="example",
        public_model_alias="example/model",
        upstream_provider="example",
        upstream_model="example/model",
        capability_profile_revision="sha256:" + "2" * 64,
        loss_matrix_revision="sha256:" + "3" * 64,
        support_status=GatewaySupportStatus.STABLE,
        reason_ids=(),
        evidence_ids=(),
    )


def _overlay_example() -> LaunchOverlayV1:
    return LaunchOverlayV1(
        route_id="example-route",
        managed_home="managed:example",
        redacted_env_delta=(),
        generated_config_refs=(),
        process_lease_ref=None,
        preflight_receipt_ref="gateway-preflight:example",
        gateway_capability_digest="4" * 64,
    )


def _receipt_example() -> GatewayPreflightReceiptV1:
    return GatewayPreflightReceiptV1(
        receipt_id="gateway-preflight-example",
        gateway_id="example",
        route_id="example-route",
        profile_digest="5" * 64,
        artifact_sha256="6" * 64,
        capability_revision="sha256:" + "7" * 64,
        models_revision="sha256:" + "8" * 64,
        loss_matrix_revision="sha256:" + "9" * 64,
        support_status=GatewaySupportStatus.STABLE,
        status=GatewayPreflightStatus.READY,
        reason_ids=(),
        checked_at="2026-08-04T00:00:00+00:00",
    )
