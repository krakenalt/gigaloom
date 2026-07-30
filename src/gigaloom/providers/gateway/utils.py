"""Private gateway URL, payload, and decoding helpers."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse

from gigaloom.config import DEFAULT_MODEL_HINTS, HarnessConfig
from gigaloom.types import GigaChatApiMode, HarnessContext

from .models import ProxyRequestError


def _sidecar_env(
    context: HarnessContext,
    api_mode: GigaChatApiMode,
    *,
    host: str,
    port: int,
    api_key: str,
    harness_model_key: str,
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GPT2GIGA_MODE": "DEV",
            "GPT2GIGA_HOST": host,
            "GPT2GIGA_PORT": str(port),
            "GPT2GIGA_ENABLE_API_KEY_AUTH": "True",
            "GPT2GIGA_API_KEY": api_key,
            "GIGALOOM_MODEL_KEY": harness_model_key,
            "GPT2GIGA_GIGACHAT_API_MODE": api_mode.value,
            "GPT2GIGA_PASS_MODEL": "False",
            "GPT2GIGA_DISABLE_REASONING": "True",
        }
    )
    if context.default_model and not env.get("GIGACHAT_MODEL"):
        env["GIGACHAT_MODEL"] = context.default_model
    return env


def _has_upstream_credentials(env: dict[str, str]) -> bool:
    for name in ("GIGACHAT_CREDENTIALS", "GIGACHAT_ACCESS_TOKEN", "GIGACHAT_USER"):
        value = env.get(name)
        if value and value.strip():
            return True
    return False


def _public_proxy_url(proxy_url: str) -> str:
    """Remove userinfo, query, and fragment from persisted proxy evidence."""
    parsed = urlparse(proxy_url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if parsed.port is not None:
        netloc = f"{host}:{parsed.port}"
    return parsed._replace(netloc=netloc, query="", fragment="").geturl()


def _model_paths(api_mode: GigaChatApiMode) -> tuple[str, ...]:
    preferred = f"/{api_mode.value}/models"
    other = "/v1/models" if api_mode == GigaChatApiMode.V2 else "/v2/models"
    return (preferred, other, "/models")


def _extract_model_ids(data: dict[str, Any]) -> list[str]:
    raw_models = data.get("data")
    if raw_models is None:
        raw_models = data.get("models")
    if not isinstance(raw_models, list):
        return []
    ids: list[str] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata")
        model_type = item.get("type")
        if model_type is None and isinstance(metadata, Mapping):
            model_type = metadata.get("type")
        if model_type is not None and model_type != "chat":
            continue
        model_id = item.get("id") or item.get("name") or item.get("model")
        if model_id:
            ids.append(str(model_id))
    return list(dict.fromkeys(ids))


def _read_error_body(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return body[:500]


def _decode_sse_json(data: str) -> dict[str, Any]:
    try:
        decoded = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProxyRequestError("proxy returned invalid JSON in SSE stream") from exc
    if not isinstance(decoded, Mapping):
        raise ProxyRequestError("proxy returned SSE JSON that is not an object")
    return dict(decoded)


def _cancel_requested(cancel_event: Any | None) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def _default_route_probe_payload(
    config: HarnessConfig,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model or config.default_model or DEFAULT_MODEL_HINTS[0],
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    return payload


def _status_indicates_mounted_route(status_code: int | None) -> bool:
    return status_code in {400, 401, 403, 405, 422}


def _route_probe_detail(status_code: int | None) -> str:
    if status_code in {400, 422}:
        return "route rejected the intentionally minimal JSON probe"
    if status_code in {401, 403}:
        return "route is protected by proxy auth"
    if status_code == 405:
        return "path exists but rejected the probe method"
    if status_code == 404:
        return "route not found"
    if status_code is None:
        return "proxy did not respond"
    return "unexpected route probe response"
