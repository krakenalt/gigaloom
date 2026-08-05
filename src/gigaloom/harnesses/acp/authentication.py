"""Provider-owned ACP authentication methods with explicit invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping

from acp.schema import (
    AuthenticateRequest,
    AuthenticateResponse,
    LogoutRequest,
    LogoutResponse,
)

from gigaloom.harnesses.acp.compatibility import require_feature, require_snapshot
from gigaloom.harnesses.acp.errors import AcpCapabilityError

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


@dataclass(frozen=True, slots=True)
class AcpAuthenticationReceiptV1:
    """Content-free result of an explicit provider-owned auth operation."""

    method_id: str
    kind: str
    state: str


def authenticate(
    client: AcpClient, *, method_id: str, explicit: bool
) -> AcpAuthenticationReceiptV1:
    """Invoke a negotiated non-terminal auth method only when explicit."""
    snapshot = require_feature(client.capability_snapshot, "authentication")
    if not explicit:
        raise AcpCapabilityError("ACP authentication requires explicit user intent")
    method = _auth_method(snapshot.auth_capabilities, method_id)
    kind = str(method.get("kind", ""))
    if kind == "terminal":
        raise AcpCapabilityError(
            "ACP terminal authentication requires a native or managed terminal"
        )
    client.request(
        "authenticate",
        AuthenticateRequest(method_id=method_id),
        AuthenticateResponse,
        error="ACP authentication response failed schema validation",
    )
    return AcpAuthenticationReceiptV1(method_id, kind, "authenticated")


def logout(client: AcpClient, *, explicit: bool) -> AcpAuthenticationReceiptV1:
    """Invoke negotiated logout only when explicitly requested."""
    snapshot = require_snapshot(client.capability_snapshot)
    if not explicit or snapshot.auth_capabilities.get("logout") is not True:
        raise AcpCapabilityError("ACP logout is unavailable or was not explicit")
    client.request(
        "logout",
        LogoutRequest(),
        LogoutResponse,
        error="ACP authentication response failed schema validation",
    )
    return AcpAuthenticationReceiptV1("logout", "provider", "logged_out")


def _auth_method(
    capabilities: Mapping[str, object], method_id: str
) -> Mapping[str, object]:
    methods = capabilities.get("methods", ())
    for item in methods if isinstance(methods, tuple) else ():
        if isinstance(item, Mapping) and item.get("id") == method_id:
            return item
    raise AcpCapabilityError("ACP authentication method was not negotiated")
