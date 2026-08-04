"""Fail-closed gpt2giga launch inputs for managed ACP agents."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Mapping, Protocol
from urllib.parse import urlsplit

from gigaloom.types import GigaChatApiMode, HarnessContext


class ManagedAcpGatewayTurn(Protocol):
    """Minimal transient turn facts required by the gateway overlay."""

    gateway_api_key: str | None
    gateway_base_url: str | None
    gateway_profile_id: str | None
    gateway_route_id: str | None
    model_id: str


@dataclass(frozen=True, slots=True)
class GatewayRouteSelection:
    """Reviewed route values safe to retain for one transient ACP turn."""

    route_id: str
    gateway_profile_id: str
    public_model_alias: str
    model_id: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)


def gateway_route_selection(
    value: object,
    context: HarnessContext,
    *,
    registry_id: str,
) -> GatewayRouteSelection | None:
    """Decode one validated ACP binding without falling back on malformed data."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("managed ACP gateway route binding is invalid")
    if value.get("schema_version") != 1 or value.get("agent_id") != "acp":
        raise ValueError("managed ACP gateway route binding is incompatible")
    gateway_profile_id = _route_text(
        value.get("gateway_profile_id"),
        "gateway profile id",
    )
    if gateway_profile_id != "gpt2giga":
        raise ValueError("managed ACP gateway profile is unsupported")
    support_status = _route_text(value.get("support_status"), "support status")
    if support_status == "blocked":
        raise ValueError("managed ACP gateway route is blocked")
    if support_status not in {
        "stable",
        "technical_preview",
        "vendor_unsupported",
    }:
        raise ValueError("managed ACP gateway support status is invalid")
    route_id = _route_text(value.get("route_id"), "gateway route id")
    public_model_alias = _route_text(
        value.get("public_model_alias"),
        "gateway model alias",
    )
    model_id = (
        f"{gateway_profile_id}/{public_model_alias}"
        if registry_id == "opencode"
        else public_model_alias
    )
    return GatewayRouteSelection(
        route_id=route_id,
        gateway_profile_id=gateway_profile_id,
        public_model_alias=public_model_alias,
        model_id=model_id,
        base_url=context.api_base_url(GigaChatApiMode.V1),
        api_key=context.api_key,
    )


def gateway_process_environment(
    request: ManagedAcpGatewayTurn,
    *,
    registry_id: str,
    root: Path,
) -> dict[str, str]:
    """Build an ephemeral OpenAI-compatible environment and OpenCode config."""
    if request.gateway_base_url is None:
        return {}
    if (
        request.gateway_route_id is None
        or request.gateway_profile_id != "gpt2giga"
        or request.model_id == "provider-default"
    ):
        raise ValueError("managed ACP gateway launch inputs are incomplete")
    base_url = _gateway_url(request.gateway_base_url)
    api_key = request.gateway_api_key or "0"
    environment = {
        "GPT2GIGA_API_KEY": api_key,
        "GPT2GIGA_BASE_URL": base_url,
        "OPENAI_API_KEY": api_key,
        "OPENAI_BASE_URL": base_url,
    }
    if registry_id != "opencode":
        return environment
    model_alias = request.model_id.removeprefix("gpt2giga/")
    if not model_alias or model_alias == request.model_id:
        raise ValueError("OpenCode gateway model selector is invalid")
    config = root / "opencode-gpt2giga.json"
    config.write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": request.model_id,
                "provider": {
                    "gpt2giga": {
                        "name": "gpt2giga",
                        "npm": "@ai-sdk/openai-compatible",
                        "models": {model_alias: {"name": model_alias}},
                        "options": {
                            "apiKey": "{env:GPT2GIGA_API_KEY}",
                            "baseURL": base_url,
                        },
                    }
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    config.chmod(0o600)
    environment["OPENCODE_CONFIG"] = os.fspath(config)
    return environment


def _route_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"managed ACP {field_name} is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 256
        or any(character in normalized for character in ("\r", "\n", "\x00"))
    ):
        raise ValueError(f"managed ACP {field_name} is invalid")
    return normalized


def _gateway_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("managed ACP gateway base URL is invalid")
    return value.rstrip("/")


__all__ = [
    "GatewayRouteSelection",
    "gateway_process_environment",
    "gateway_route_selection",
]
