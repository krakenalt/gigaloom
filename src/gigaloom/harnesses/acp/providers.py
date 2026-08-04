"""Bounded configurable-provider methods from the draft ACP contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from acp.schema import (
    DisableProviderRequest,
    DisableProviderResponse,
    ListProvidersRequest,
    ListProvidersResponse,
    SetProviderRequest,
    SetProviderResponse,
)
from pydantic import ValidationError

from gigaloom.harnesses.acp.compatibility import require_feature
from gigaloom.harnesses.acp.errors import AcpCapabilityError, AcpProtocolError

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


LIST_PROVIDERS_METHOD = "providers/list"
SET_PROVIDER_METHOD = "providers/set"
DISABLE_PROVIDER_METHOD = "providers/disable"
MAX_PROVIDERS = 64
MAX_PROVIDER_API_TYPES = 16
MAX_PROVIDER_HEADERS = 32
MAX_PROVIDER_TEXT = 2048


@dataclass(frozen=True, slots=True)
class AcpProviderV1:
    """Content-free provider capability and effective routing projection."""

    provider_id: str
    supported_api_types: tuple[str, ...]
    required: bool
    current_api_type: str | None
    current_base_url: str | None


def list_providers(client: AcpClient) -> tuple[AcpProviderV1, ...]:
    """List one bounded, strictly validated provider capability snapshot."""
    require_feature(client.capability_snapshot, "provider_configuration")
    request = ListProvidersRequest()
    raw = client.supervisor.request(
        LIST_PROVIDERS_METHOD,
        _wire(request),
        timeout=client.limits.request_timeout_seconds,
    )
    response = _validate_response(
        ListProvidersResponse,
        raw,
        allowed_keys=frozenset({"providers", "_meta"}),
        operation="list",
    )
    if len(response.providers) > MAX_PROVIDERS:
        raise AcpProtocolError("ACP provider list exceeds the configured bound")
    providers = tuple(_project_provider(item) for item in response.providers)
    identifiers = tuple(item.provider_id for item in providers)
    if len(set(identifiers)) != len(identifiers):
        raise AcpProtocolError("ACP provider list contains duplicate ids")
    return providers


def configure_provider(
    client: AcpClient,
    *,
    api_type: str,
    base_url: str,
    headers: Mapping[str, str] | None = None,
) -> AcpProviderV1:
    """Configure the single matching provider and verify its effective route."""
    requested_api_type = _bounded_text(api_type, field_name="ACP provider api type")
    requested_base_url = _bounded_text(base_url, field_name="ACP provider base URL")
    safe_headers = _headers(headers)
    candidates = tuple(
        item
        for item in list_providers(client)
        if requested_api_type in item.supported_api_types
    )
    if not candidates:
        raise AcpCapabilityError("ACP provider protocol is not supported")
    if len(candidates) != 1:
        raise AcpProtocolError("ACP provider selection is ambiguous")
    selected = candidates[0]
    request = SetProviderRequest(
        id=selected.provider_id,
        api_type=requested_api_type,
        base_url=requested_base_url,
        headers=safe_headers or None,
    )
    raw = client.supervisor.request(
        SET_PROVIDER_METHOD,
        _wire(request),
        timeout=client.limits.request_timeout_seconds,
    )
    _validate_response(
        SetProviderResponse,
        raw,
        allowed_keys=frozenset({"_meta"}),
        operation="set",
    )
    matches = tuple(
        item
        for item in list_providers(client)
        if item.provider_id == selected.provider_id
    )
    if len(matches) != 1:
        raise AcpProtocolError("ACP provider disappeared after configuration")
    effective = matches[0]
    if (
        effective.current_api_type != requested_api_type
        or effective.current_base_url != requested_base_url
    ):
        raise AcpProtocolError("ACP provider configuration was not applied")
    return effective


def disable_provider(client: AcpClient, *, provider_id: str) -> None:
    """Disable one advertised optional provider using the bounded draft method."""
    requested_id = _bounded_text(provider_id, field_name="ACP provider id")
    matches = tuple(
        item for item in list_providers(client) if item.provider_id == requested_id
    )
    if len(matches) != 1:
        raise AcpCapabilityError("ACP provider id is not advertised")
    if matches[0].required:
        raise AcpCapabilityError("ACP required provider cannot be disabled")
    request = DisableProviderRequest(id=requested_id)
    raw = client.supervisor.request(
        DISABLE_PROVIDER_METHOD,
        _wire(request),
        timeout=client.limits.request_timeout_seconds,
    )
    _validate_response(
        DisableProviderResponse,
        raw,
        allowed_keys=frozenset({"_meta"}),
        operation="disable",
    )


def _project_provider(value: Any) -> AcpProviderV1:
    provider_id = _bounded_text(value.id, field_name="ACP provider id")
    supported = tuple(
        _bounded_text(item, field_name="ACP provider api type")
        for item in value.supported
    )
    if not supported or len(supported) > MAX_PROVIDER_API_TYPES:
        raise AcpProtocolError("ACP provider api types are invalid or unbounded")
    if len(set(supported)) != len(supported):
        raise AcpProtocolError("ACP provider api types contain duplicates")
    current = value.current
    return AcpProviderV1(
        provider_id=provider_id,
        supported_api_types=supported,
        required=value.required,
        current_api_type=(
            _bounded_text(current.api_type, field_name="ACP provider current api type")
            if current is not None
            else None
        ),
        current_base_url=(
            _bounded_text(current.base_url, field_name="ACP provider current base URL")
            if current is not None
            else None
        ),
    )


def _headers(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_PROVIDER_HEADERS:
        raise ValueError("ACP provider headers exceed the configured bound")
    result: dict[str, str] = {}
    for name, secret in value.items():
        safe_name = _bounded_text(name, field_name="ACP provider header name")
        if safe_name in result:
            raise ValueError("ACP provider header names must be unique")
        if not isinstance(secret, str) or not secret or len(secret) > MAX_PROVIDER_TEXT:
            raise ValueError("ACP provider header value is invalid")
        result[safe_name] = secret
    return result


def _bounded_text(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value) > MAX_PROVIDER_TEXT
    ):
        raise AcpProtocolError(f"{field_name} is invalid")
    return value


def _wire(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True, exclude_none=True)


def _validate_response(
    model: Any,
    raw: Mapping[str, Any],
    *,
    allowed_keys: frozenset[str],
    operation: str,
) -> Any:
    if set(raw) - allowed_keys:
        raise AcpProtocolError(
            f"ACP provider {operation} response contains unexpected fields"
        )
    try:
        return model.model_validate(raw)
    except (TypeError, ValueError, ValidationError) as exc:
        raise AcpProtocolError(
            f"ACP provider {operation} response failed schema validation"
        ) from exc
