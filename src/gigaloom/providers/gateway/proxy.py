"""Small synchronous client helpers for the local gpt2giga proxy."""

from __future__ import annotations

from collections.abc import Iterator
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gigaloom.config import DEFAULT_MODEL_HINTS, HarnessConfig
from gigaloom.providers.gateway.preset import (
    GPT2GIGA_PRESET_EXTRA,
    gpt2giga_preset_available,
)
from gigaloom.types import GigaChatApiMode, HarnessContext, redact_secrets


def build_chat_completions_url(
    proxy_url: str,
    api_mode: GigaChatApiMode,
) -> str:
    """Return explicit v1/v2 Chat Completions URL."""
    return f"{proxy_url.rstrip('/')}/{api_mode.value}/chat/completions"


def request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Send a JSON request and return decoded JSON."""
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["x-api-key"] = api_key
    request = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read()
    except HTTPError as exc:
        error_body = _read_error_body(exc)
        message = f"proxy returned HTTP {exc.code}"
        if error_body:
            message = f"{message}: {error_body}"
        raise ProxyRequestError(message, status_code=exc.code) from exc
    except URLError as exc:
        raise ProxyRequestError(f"proxy is not reachable: {exc.reason}") from exc
    if not data:
        return {}
    try:
        decoded = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProxyRequestError("proxy returned non-JSON response") from exc
    if not isinstance(decoded, dict):
        raise ProxyRequestError("proxy returned JSON that is not an object")
    return decoded


def health_check(config: HarnessConfig) -> ProxyHealth:
    """Check common local proxy health paths without requiring credentials."""
    return _health_check_url(config.proxy_url)


def sidecar_preflight(context: HarnessContext) -> SidecarPreflight:
    """Validate whether this process can start a local gpt2giga sidecar."""
    if not context.auto_start_proxy:
        return SidecarPreflight(ok=False, reason="auto-start disabled")
    parsed = urlparse(context.proxy_url)
    if parsed.scheme != "http":
        return SidecarPreflight(
            ok=False,
            reason="auto-start supports only http:// local proxy URLs",
        )
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return SidecarPreflight(
            ok=False,
            reason="auto-start is limited to 127.0.0.1, localhost, or ::1",
        )
    path = parsed.path.rstrip("/")
    if path:
        return SidecarPreflight(
            ok=False,
            reason="auto-start requires a proxy URL without a path component",
        )
    if not gpt2giga_preset_available():
        return SidecarPreflight(
            ok=False,
            reason=(
                "optional gpt2giga preset is not installed; install "
                f"gigaloom[{GPT2GIGA_PRESET_EXTRA}]"
            ),
        )
    if not _has_upstream_credentials(os.environ):
        return SidecarPreflight(
            ok=False,
            reason=(
                "missing GigaChat credentials; set GIGACHAT_CREDENTIALS or "
                "GIGACHAT_ACCESS_TOKEN"
            ),
        )
    return SidecarPreflight(ok=True, reason="ready")


def cached_sidecar_api_key(proxy_url: str) -> str | None:
    """Return the generated sidecar API key for this process, if any."""
    return _SIDECAR_API_KEYS.get(proxy_url)


def cached_sidecar_model_key(proxy_url: str) -> str | None:
    """Return the generated sidecar model-signing key for this process."""
    return _SIDECAR_MODEL_KEYS.get(proxy_url)


def ensure_proxy_available(
    context: HarnessContext,
    api_mode: GigaChatApiMode,
    *,
    use_cached_sidecar_key: bool = True,
) -> ProxyStartup:
    """Return a reachable proxy or start a local sidecar when allowed."""
    cached_api_key = context.api_key
    if cached_api_key is None and use_cached_sidecar_key:
        cached_api_key = cached_sidecar_api_key(context.proxy_url)
    harness_model_key = context.harness_model_key or cached_sidecar_model_key(
        context.proxy_url
    )
    health = _health_check_url(context.proxy_url)
    if health.ok:
        return ProxyStartup(
            ok=True,
            proxy_url=context.proxy_url,
            api_key=cached_api_key,
            harness_model_key=harness_model_key,
            health_path=health.path,
            health_status_code=health.status_code,
            detail=f"proxy already reachable via {health.path}",
        )

    preflight = sidecar_preflight(context)
    if not preflight.ok:
        return ProxyStartup(
            ok=False,
            proxy_url=context.proxy_url,
            api_key=cached_api_key,
            harness_model_key=harness_model_key,
            error=preflight.reason,
        )

    return _start_local_sidecar(context, api_mode, cached_api_key)


def ensure_proxy_route_available(
    context: HarnessContext,
    api_mode: GigaChatApiMode,
) -> ProxyRoutePreflight:
    """Prepare a proxy and prove the selected compatibility route is usable."""
    route_path = f"/{api_mode.value}/models"
    startup = ensure_proxy_available(
        context,
        api_mode,
        use_cached_sidecar_key=False,
    )
    if not startup.ok:
        return ProxyRoutePreflight(
            ok=False,
            proxy_url=context.proxy_url,
            api_mode=api_mode,
            route_path=route_path,
            startup=startup,
            error=redact_secrets(startup.error or "proxy is not reachable"),
        )
    try:
        request_json(
            "GET",
            f"{context.proxy_url.rstrip('/')}{route_path}",
            api_key=startup.api_key or context.api_key,
            timeout=5,
        )
    except ProxyRequestError as exc:
        stop_owned_sidecar(startup)
        if exc.status_code in {401, 403}:
            error = (
                f"proxy route {route_path} rejected authentication; configure "
                "GPT2GIGA_HARNESS_API_KEY for an existing auth-enabled proxy"
            )
        else:
            error = f"proxy compatibility route {route_path} is unavailable: {exc}"
        return ProxyRoutePreflight(
            ok=False,
            proxy_url=context.proxy_url,
            api_mode=api_mode,
            route_path=route_path,
            startup=startup,
            status_code=exc.status_code,
            error=redact_secrets(error),
        )
    return ProxyRoutePreflight(
        ok=True,
        proxy_url=context.proxy_url,
        api_mode=api_mode,
        route_path=route_path,
        startup=startup,
        status_code=200,
        detail="selected compatibility route accepted authenticated model discovery",
    )


def proxy_route_preflight_to_dict(
    result: ProxyRoutePreflight,
) -> dict[str, Any]:
    """Serialize route evidence without exposing the transient proxy key."""
    ownership = "owned" if result.startup.started else "external"
    return {
        "ok": result.ok,
        "proxy_url": _public_proxy_url(result.proxy_url),
        "api_mode": result.api_mode.value,
        "route_path": result.route_path,
        "route_status_code": result.status_code,
        "health_path": result.startup.health_path,
        "health_status_code": result.startup.health_status_code,
        "auth": "configured" if result.api_key else "not_configured",
        "ownership": ownership,
        "sidecar_pid": result.startup.pid if result.startup.started else None,
        "ownership_id": (
            result.startup.ownership_id if result.startup.started else None
        ),
        "detail": redact_secrets(result.detail or result.startup.detail),
        "error": redact_secrets(result.error),
    }


def stop_owned_sidecar(startup: ProxyStartup) -> bool:
    """Stop a sidecar only when this process owns the exact startup handle."""
    ownership_id = startup.ownership_id
    if not startup.started or ownership_id is None:
        return False
    with _OWNED_SIDECARS_LOCK:
        process = _OWNED_SIDECARS.pop(ownership_id, None)
    if process is None:
        return False
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if startup.proxy_url and startup.api_key == _SIDECAR_API_KEYS.get(
        startup.proxy_url
    ):
        _SIDECAR_API_KEYS.pop(startup.proxy_url, None)
        _SIDECAR_MODEL_KEYS.pop(startup.proxy_url, None)
    return True


def _health_check_url(proxy_url: str) -> ProxyHealth:
    """Check common local proxy health paths without requiring credentials."""
    last_error = "proxy did not respond"
    for path in ("/health", "/ping", "/"):
        url = f"{proxy_url}{path}"
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                return ProxyHealth(
                    ok=200 <= response.status < 500,
                    url=proxy_url,
                    path=path,
                    status_code=response.status,
                )
        except HTTPError as exc:
            if exc.code < 500:
                return ProxyHealth(
                    ok=True,
                    url=proxy_url,
                    path=path,
                    status_code=exc.code,
                )
        except URLError as exc:
            last_error = str(exc.reason)
    return ProxyHealth(ok=False, url=proxy_url, error=last_error)


def probe_json_route(
    config: HarnessConfig,
    path: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    model: str | None = None,
) -> RouteProbe:
    """Probe whether a JSON route is mounted without requiring upstream success."""
    normalized_path = path if path.startswith("/") else f"/{path}"
    try:
        request_json(
            method,
            f"{config.proxy_url}{normalized_path}",
            payload=payload
            if payload is not None
            else _default_route_probe_payload(config, model=model),
            api_key=config.api_key or cached_sidecar_api_key(config.proxy_url),
            timeout=5,
        )
    except ProxyRequestError as exc:
        status_code = exc.status_code
        return RouteProbe(
            ok=_status_indicates_mounted_route(status_code),
            path=normalized_path,
            method=method.upper(),
            status_code=status_code,
            detail=_route_probe_detail(status_code),
            error=str(exc),
        )
    return RouteProbe(
        ok=True,
        path=normalized_path,
        method=method.upper(),
        status_code=200,
        detail="accepted probe request",
    )


def discover_models(
    config: HarnessConfig,
    api_mode: GigaChatApiMode,
    *,
    include_compat_paths: bool = True,
    include_fallback: bool = True,
) -> ModelDiscovery:
    """Try proxy model endpoints and fall back to local hints."""
    paths = (
        _model_paths(api_mode)
        if include_compat_paths
        else (f"/{api_mode.value}/models",)
    )
    errors: list[str] = []
    for path in paths:
        try:
            data = request_json(
                "GET",
                f"{config.proxy_url}{path}",
                api_key=config.api_key or cached_sidecar_api_key(config.proxy_url),
                timeout=10,
            )
        except ProxyRequestError as exc:
            errors.append(f"{path}: {exc}")
            continue
        models = _extract_model_ids(data)
        if models:
            return ModelDiscovery(ok=True, models=tuple(models), source=path)

    if not include_fallback:
        return ModelDiscovery(
            ok=False,
            models=(),
            source=paths[0],
            error="; ".join(errors) or f"{paths[0]} returned no models",
        )

    fallback = tuple(
        model for model in (config.default_model, *DEFAULT_MODEL_HINTS) if model
    )
    return ModelDiscovery(
        ok=False,
        models=tuple(dict.fromkeys(fallback)),
        source="fallback",
        error="; ".join(errors) or "model discovery failed",
    )


def extract_text(data: dict[str, Any]) -> str:
    """Extract text from common Chat Completions-like response shapes."""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            delta = first.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    return content
            text = first.get("text")
            if isinstance(text, str):
                return text
    for key in ("output_text", "text"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def safe_raw(data: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted shallow JSON object for results."""
    return redact_secrets(data)


def _start_local_sidecar(
    context: HarnessContext,
    api_mode: GigaChatApiMode,
    api_key: str | None,
) -> ProxyStartup:
    parsed = urlparse(context.proxy_url)
    port = parsed.port or 80
    bind_host = "::1" if parsed.hostname == "::1" else "127.0.0.1"
    api_key = api_key or secrets.token_urlsafe(32)
    harness_model_key = secrets.token_urlsafe(32)
    env = _sidecar_env(
        context,
        api_mode,
        host=bind_host,
        port=port,
        api_key=api_key,
        harness_model_key=harness_model_key,
    )
    process = subprocess.Popen(
        [sys.executable, "-c", f"from {'gpt2giga'} import run; run()"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + context.proxy_start_timeout_seconds
    ownership_id = f"sidecar_{secrets.token_hex(12)}"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return ProxyStartup(
                ok=False,
                proxy_url=context.proxy_url,
                api_key=api_key,
                harness_model_key=harness_model_key,
                error=f"proxy sidecar exited early with code {process.returncode}",
            )
        health = _health_check_url(context.proxy_url)
        if health.ok:
            _SIDECAR_API_KEYS[context.proxy_url] = api_key
            _SIDECAR_MODEL_KEYS[context.proxy_url] = harness_model_key
            with _OWNED_SIDECARS_LOCK:
                _OWNED_SIDECARS[ownership_id] = process
            return ProxyStartup(
                ok=True,
                proxy_url=context.proxy_url,
                started=True,
                api_key=api_key,
                harness_model_key=harness_model_key,
                pid=process.pid,
                ownership_id=ownership_id,
                health_path=health.path,
                health_status_code=health.status_code,
                detail=f"started local proxy sidecar on port {port}",
            )
        time.sleep(0.2)

    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
    return ProxyStartup(
        ok=False,
        proxy_url=context.proxy_url,
        api_key=api_key,
        harness_model_key=harness_model_key,
        error=(
            f"timed out waiting for local proxy sidecar to start at {context.proxy_url}"
        ),
    )


from .models import (  # noqa: E402, F401
    ModelDiscovery,
    ProxyHealth,
    ProxyRequestError,
    ProxyRoutePreflight,
    ProxyStartup,
    RouteProbe,
    SidecarPreflight,
)
from . import transport as _transport  # noqa: E402
from .utils import (  # noqa: E402
    _default_route_probe_payload,
    _extract_model_ids,
    _has_upstream_credentials,
    _model_paths,
    _public_proxy_url,
    _read_error_body,
    _route_probe_detail,
    _sidecar_env,
    _status_indicates_mounted_route,
)

_SIDECAR_API_KEYS: dict[str, str] = {}
_SIDECAR_MODEL_KEYS: dict[str, str] = {}
_OWNED_SIDECARS: dict[str, subprocess.Popen[Any]] = {}
_OWNED_SIDECARS_LOCK = threading.RLock()


def upload_file(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Delegate one upload while preserving the legacy transport patch seam."""
    _transport.urlopen = urlopen
    return _transport.upload_file(*args, **kwargs)


def stream_sse_json(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Delegate one SSE request while preserving the legacy transport patch seam."""
    _transport.urlopen = urlopen
    yield from _transport.stream_sse_json(*args, **kwargs)
