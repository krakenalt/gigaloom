"""ACP initialize capability and loss projections."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from acp.schema import InitializeResponse

from gigaloom.harnesses.acp.contracts import (
    AcpCapabilitySnapshotV1,
    AcpClientInfo,
    AcpImplementationInfo,
    CapabilityLoss,
    JsonValue,
    NegotiatedFeature,
)


_OPTIONAL_SESSION_FEATURES = {
    "session_list": "list",
    "session_delete": "delete",
    "session_resume": "resume",
    "session_close": "close",
}


def build_capability_snapshot(
    response: InitializeResponse,
    *,
    client_info: AcpClientInfo,
    compatibility_profile_digest: str,
    process_fingerprint: str,
    connection_generation: int,
) -> AcpCapabilitySnapshotV1:
    """Create a content-free capability snapshot from one validated response."""
    payload = response.model_dump(mode="json", by_alias=True, exclude_none=True)
    raw_capabilities = _mapping(payload.get("agentCapabilities"))
    agent_capabilities = _stable_capabilities(raw_capabilities)
    session_capabilities = _stable_capabilities(
        _mapping(raw_capabilities.get("sessionCapabilities"))
    )
    auth_capabilities = _auth_projection(payload, raw_capabilities)
    negotiated, unsupported = _feature_matrix(
        agent_capabilities, session_capabilities, auth_capabilities
    )
    client = AcpImplementationInfo(
        name=client_info.name,
        title=client_info.title,
        version=client_info.version,
    )
    agent = (
        AcpImplementationInfo(
            name=response.agent_info.name,
            title=response.agent_info.title,
            version=response.agent_info.version,
        )
        if response.agent_info is not None
        else None
    )
    digest_payload = {
        "protocol_version": str(response.protocol_version),
        "client_info": _implementation_payload(client),
        "agent_info": _implementation_payload(agent) if agent else None,
        "agent_capabilities": agent_capabilities,
        "session_capabilities": session_capabilities,
        "auth_capabilities": auth_capabilities,
        "negotiated_features": [
            {"feature": item.feature, "state": item.state} for item in negotiated
        ],
        "unsupported_features": [
            {
                "feature": item.feature,
                "state": item.state,
                "reason": item.reason,
            }
            for item in unsupported
        ],
        "compatibility_profile_digest": compatibility_profile_digest,
        "process_fingerprint": process_fingerprint,
        "connection_generation": connection_generation,
    }
    snapshot_digest = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return AcpCapabilitySnapshotV1(
        protocol_version=str(response.protocol_version),
        client_info=client,
        agent_info=agent,
        agent_capabilities=agent_capabilities,
        session_capabilities=session_capabilities,
        auth_capabilities=auth_capabilities,
        negotiated_features=negotiated,
        unsupported_features=unsupported,
        compatibility_profile_digest=compatibility_profile_digest,
        process_fingerprint=process_fingerprint,
        connection_generation=connection_generation,
        snapshot_digest=snapshot_digest,
    )


def _feature_matrix(
    agent: Mapping[str, JsonValue],
    sessions: Mapping[str, JsonValue],
    auth: Mapping[str, JsonValue],
) -> tuple[tuple[NegotiatedFeature, ...], tuple[CapabilityLoss, ...]]:
    negotiated = [
        NegotiatedFeature("structured_prompt"),
        NegotiatedFeature("session_new"),
        NegotiatedFeature("cancellation"),
    ]
    unsupported: list[CapabilityLoss] = []
    if agent.get("loadSession") is True:
        negotiated.append(NegotiatedFeature("session_load"))
    else:
        unsupported.append(
            CapabilityLoss("session_load", "unsupported", "not_advertised")
        )
    for feature, field_name in _OPTIONAL_SESSION_FEATURES.items():
        if isinstance(sessions.get(field_name), Mapping):
            negotiated.append(NegotiatedFeature(feature))
        else:
            unsupported.append(CapabilityLoss(feature, "unsupported", "not_advertised"))
    if auth.get("methods"):
        negotiated.append(NegotiatedFeature("authentication", "provider_owned"))
    else:
        unsupported.append(
            CapabilityLoss("authentication", "unsupported", "not_advertised")
        )
    return tuple(negotiated), tuple(unsupported)


def _auth_projection(
    payload: Mapping[str, Any], capabilities: Mapping[str, Any]
) -> dict[str, JsonValue]:
    methods: list[JsonValue] = []
    for raw in payload.get("authMethods", []):
        item = _mapping(raw)
        projection: dict[str, JsonValue] = {
            "id": str(item.get("id", "")),
            "kind": (
                "env_var"
                if "vars" in item
                else "terminal"
                if "args" in item or "env" in item
                else "agent"
            ),
        }
        if "vars" in item:
            projection["environment_variables"] = tuple(
                str(variable.get("name", ""))
                for variable in item["vars"]
                if isinstance(variable, Mapping)
            )
        methods.append(projection)
    raw_auth = _mapping(capabilities.get("auth"))
    return {
        "methods": tuple(methods),
        "logout": isinstance(raw_auth.get("logout"), Mapping),
    }


def _stable_capabilities(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return {
        str(key): _stable_json(item)
        for key, item in sorted(value.items())
        if key not in {"_meta", "nes", "positionEncoding", "providers"}
    }


def _stable_json(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return _stable_capabilities(value)
    if isinstance(value, list):
        return tuple(_stable_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("ACP capability response contains a non-JSON value")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _implementation_payload(value: AcpImplementationInfo) -> dict[str, Any]:
    return {"name": value.name, "title": value.title, "version": value.version}
