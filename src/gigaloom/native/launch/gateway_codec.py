"""Strict canonical serialization for gateway launch contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields
from enum import Enum
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
_ContractT = TypeVar(
    "_ContractT",
    GatewayProfileV1,
    BridgeRouteV1,
    LaunchOverlayV1,
    GatewayPreflightReceiptV1,
)
_GatewayContract = (
    GatewayProfileV1 | BridgeRouteV1 | LaunchOverlayV1 | GatewayPreflightReceiptV1
)
_GatewayContractType = (
    type[GatewayProfileV1]
    | type[BridgeRouteV1]
    | type[LaunchOverlayV1]
    | type[GatewayPreflightReceiptV1]
)


def gateway_profile_to_dict(value: GatewayProfileV1) -> dict[str, Any]:
    """Serialize one exact gateway profile."""
    return _contract_to_dict(value)


def gateway_profile_from_dict(payload: Mapping[str, Any]) -> GatewayProfileV1:
    """Decode one strict gateway profile."""
    return _contract_from_dict(
        payload,
        GatewayProfileV1,
        "profile",
        schema_version=_integer,
        mode=lambda value, name: _enum(GatewayMode, value, name),
        auth_ref=_optional_string,
        tls_policy_ref=_optional_string,
    )


def bridge_route_to_dict(value: BridgeRouteV1) -> dict[str, Any]:
    """Serialize one immutable reviewed bridge route."""
    return _contract_to_dict(value)


def bridge_route_from_dict(payload: Mapping[str, Any]) -> BridgeRouteV1:
    """Decode one strict bridge route."""
    return _contract_from_dict(
        payload,
        BridgeRouteV1,
        "route",
        schema_version=_integer,
        support_status=lambda value, name: _enum(GatewaySupportStatus, value, name),
        reason_ids=_string_tuple,
        evidence_ids=_string_tuple,
        reasoning_selector=_optional_string,
        required_acknowledgement=_optional_string,
    )


def launch_overlay_to_dict(value: LaunchOverlayV1) -> dict[str, Any]:
    """Serialize a redaction-safe launch overlay."""
    return _contract_to_dict(value, mapping_fields=frozenset({"redacted_env_delta"}))


def launch_overlay_from_dict(payload: Mapping[str, Any]) -> LaunchOverlayV1:
    """Decode one strict redaction-safe launch overlay."""
    return _contract_from_dict(
        payload,
        LaunchOverlayV1,
        "overlay",
        schema_version=_integer,
        redacted_env_delta=_string_pairs,
        generated_config_refs=_string_tuple,
        process_lease_ref=_optional_string,
    )


def gateway_preflight_receipt_to_dict(
    value: GatewayPreflightReceiptV1,
) -> dict[str, Any]:
    """Serialize a content-free gateway preflight receipt."""
    return _contract_to_dict(value)


def gateway_preflight_receipt_from_dict(
    payload: Mapping[str, Any],
) -> GatewayPreflightReceiptV1:
    """Decode one strict gateway preflight receipt."""
    return _contract_from_dict(
        payload,
        GatewayPreflightReceiptV1,
        "preflight receipt",
        schema_version=_integer,
        support_status=lambda value, name: _enum(GatewaySupportStatus, value, name),
        status=lambda value, name: _enum(GatewayPreflightStatus, value, name),
        reason_ids=_string_tuple,
    )


def gateway_contract_digest(value: object) -> str:
    """Return the canonical digest for one supported gateway contract."""
    if not isinstance(
        value,
        (GatewayProfileV1, BridgeRouteV1, LaunchOverlayV1, GatewayPreflightReceiptV1),
    ):
        raise TypeError("unsupported gateway contract type")
    mapping_fields = (
        frozenset({"redacted_env_delta"})
        if isinstance(value, LaunchOverlayV1)
        else frozenset()
    )
    return canonical_digest(_contract_to_dict(value, mapping_fields=mapping_fields))


def _strict(
    payload: Mapping[str, Any],
    contract_type: _GatewayContractType,
    field_name: str,
) -> Mapping[str, Any]:
    return require_mapping(
        payload,
        required={field.name for field in fields(contract_type)},
        field_name=field_name,
    )


def _contract_to_dict(
    value: _GatewayContract,
    *,
    mapping_fields: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if field.name in mapping_fields:
            item = dict(item)
        elif isinstance(item, Enum):
            item = item.value
        elif isinstance(item, tuple):
            item = list(item)
        payload[field.name] = item
    return payload


def _contract_from_dict(
    payload: Mapping[str, Any],
    contract_type: type[_ContractT],
    field_name: str,
    **decoders: Callable[[object, str], object],
) -> _ContractT:
    values = _strict(payload, contract_type, field_name)
    decoded = {
        field.name: decoders.get(field.name, _string)(values[field.name], field.name)
        for field in fields(contract_type)
    }
    return cast(_ContractT, cast(Any, contract_type)(**decoded))


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


def _string_pairs(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} must be an object")
    return tuple(
        sorted(
            (
                _string(key, f"{field_name} key"),
                _string(item, f"{field_name}.{key}"),
            )
            for key, item in value.items()
        )
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
