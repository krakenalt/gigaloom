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
    ProviderInfo,
    SetProviderRequest,
    SetProviderResponse,
)
from gigaloom.harnesses.acp.compatibility import require_feature
from gigaloom.harnesses.acp.errors import AcpCapabilityError, AcpProtocolError

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


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
    response = _request(
        client, "providers/list", ListProvidersRequest(), ListProvidersResponse
    )
    if len(response.providers) > MAX_PROVIDERS:
        raise AcpProtocolError("ACP provider list exceeds the configured bound")
    providers = tuple(_project_provider(item) for item in response.providers)
    if len({item.provider_id for item in providers}) != len(providers):
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
    requested_api_type = _text(api_type, "api type")
    requested_base_url = _text(base_url, "base URL")
    candidates = [
        item
        for item in list_providers(client)
        if requested_api_type in item.supported_api_types
    ]
    if not candidates:
        raise AcpCapabilityError("ACP provider protocol is not supported")
    if len(candidates) != 1:
        raise AcpProtocolError("ACP provider selection is ambiguous")
    selected = candidates[0]
    _request(
        client,
        "providers/set",
        SetProviderRequest(
            id=selected.provider_id,
            api_type=requested_api_type,
            base_url=requested_base_url,
            headers=_headers(headers) or None,
        ),
        SetProviderResponse,
    )
    matches = [
        item
        for item in list_providers(client)
        if item.provider_id == selected.provider_id
    ]
    if len(matches) != 1:
        raise AcpProtocolError("ACP provider disappeared after configuration")
    effective = matches[0]
    if (effective.current_api_type, effective.current_base_url) != (
        requested_api_type,
        requested_base_url,
    ):
        raise AcpProtocolError("ACP provider configuration was not applied")
    return effective


def disable_provider(client: AcpClient, *, provider_id: str) -> None:
    """Disable one advertised optional provider using the bounded draft method."""
    requested_id = _text(provider_id, "id")
    matches = [
        item for item in list_providers(client) if item.provider_id == requested_id
    ]
    if len(matches) != 1:
        raise AcpCapabilityError("ACP provider id is not advertised")
    if matches[0].required:
        raise AcpCapabilityError("ACP required provider cannot be disabled")
    _request(
        client,
        "providers/disable",
        DisableProviderRequest(id=requested_id),
        DisableProviderResponse,
    )


def _request(client: AcpClient, method: str, request: Any, response_type: Any) -> Any:
    return client.request(
        method,
        request,
        response_type,
        error=(
            f"ACP provider {method.removeprefix('providers/')} response failed "
            "schema validation"
        ),
        forbid_extra=True,
    )


def _project_provider(value: ProviderInfo) -> AcpProviderV1:
    supported = tuple(_text(item, "api type") for item in value.supported)
    if not 0 < len(supported) <= MAX_PROVIDER_API_TYPES or len(set(supported)) != len(
        supported
    ):
        raise AcpProtocolError("ACP provider api types are invalid or duplicated")
    current = value.current
    return AcpProviderV1(
        _text(value.id, "id"),
        supported,
        value.required,
        _text(current.api_type, "current api type") if current else None,
        _text(current.base_url, "current base URL") if current else None,
    )


def _headers(value: Mapping[str, str] | None) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_PROVIDER_HEADERS:
        raise ValueError("ACP provider headers exceed the configured bound")
    result: dict[str, str] = {}
    for name, secret in value.items():
        safe_name = _text(name, "header name")
        if not isinstance(secret, str) or not secret or len(secret) > MAX_PROVIDER_TEXT:
            raise ValueError("ACP provider header value is invalid")
        result[safe_name] = secret
    return result


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise AcpProtocolError(f"ACP provider {field_name} is invalid")
    if not value or "\x00" in value or len(value) > MAX_PROVIDER_TEXT:
        raise AcpProtocolError(f"ACP provider {field_name} is invalid")
    return value
