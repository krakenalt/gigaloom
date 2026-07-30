"""Shared doctor report models, bounds, and normalization helpers."""

from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness import proxy
from gpt2giga_harness.config import DEFAULT_MODEL_HINTS, HarnessConfig
from gpt2giga_harness.types import redact_secrets

DOCTOR_SCHEMA_VERSION = 2
DOCTOR_REPORT_KIND = "gpt2giga_harness_doctor_report"
_WORKER_STALE_AFTER_SECONDS = 30.0
_MAX_SNAPSHOT_VALIDATIONS = 100
_MAX_SNAPSHOT_BYTES = 1_000_000
_MAX_DOCTOR_CHECKS = 128
_GUIDED_DOMAINS = (
    "provider",
    "ui_identity",
    "worker",
    "git",
    "github",
    "network",
    "mcp",
    "skills",
    "plugins",
)


def _check(
    check_id: str,
    category: str,
    status: str,
    summary: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    remediation: tuple[dict[str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "id": check_id,
        "category": category,
        "status": status,
        "summary": summary,
        "evidence": dict(evidence or {}),
        "remediation": list(remediation),
    }


def _remedy(message: str, command: str) -> dict[str, str]:
    return {"message": message, "command": command}


def _disabled_actions(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = []
    for check in checks:
        if check["status"] == "ready":
            continue
        remediation = check.get("remediation") or []
        first = remediation[0] if remediation else {}
        actions.append(
            {
                "check_id": check["id"],
                "status": check["status"],
                "reason": f"{check['id']}_{check['status']}",
                "recovery": first.get("message"),
                "command": first.get("command"),
            }
        )
    return actions[:_MAX_DOCTOR_CHECKS]


def _safe_enum(value: Any, allowed: set[str]) -> str | None:
    text = str(value) if value is not None else None
    return text if text in allowed else None


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        _sanitize_report(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sanitize_report(value: Any) -> Any:
    """Redact secrets and collapse the operator home in diagnostic strings."""
    value = redact_secrets(value)
    if isinstance(value, Mapping):
        return {str(key): _sanitize_report(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_sanitize_report(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_report(item) for item in value]
    if isinstance(value, str):
        home = str(Path.home())
        return value.replace(home, "~") if home else value
    return value


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _optional_package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def _health_text(health: proxy.ProxyHealth) -> str:
    if health.ok:
        return f"OK via {health.path} ({health.status_code})"
    return f"unreachable ({health.error})"


def _sidecar_text(sidecar: proxy.SidecarPreflight) -> str:
    if sidecar.ok:
        return "ready"
    return sidecar.reason


def _chat_route_probes(
    config: HarnessConfig,
    model: str,
) -> dict[str, proxy.RouteProbe]:
    return {
        path: proxy.probe_json_route(config, path, model=model)
        for path in ("/v1/chat/completions", "/v2/chat/completions")
    }


def _route_probe_model(
    config: HarnessConfig,
    models: proxy.ModelDiscovery,
) -> str:
    if config.default_model:
        return config.default_model
    if models.models:
        return models.models[0]
    return DEFAULT_MODEL_HINTS[0]


def _route_probe_text(route: proxy.RouteProbe | None) -> str:
    if route is None:
        return "not checked; proxy unreachable"
    status = (
        f"HTTP {route.status_code}" if route.status_code is not None else "no status"
    )
    detail = f"; {route.detail}" if route.detail else ""
    if route.ok:
        return f"reachable ({status}{detail})"
    return f"unreachable ({status}{detail})"


def _credentials_source() -> str | None:
    for name in (
        "GIGACHAT_CREDENTIALS",
        "GIGACHAT_ACCESS_TOKEN",
        "GIGACHAT_USER",
    ):
        value = os.getenv(name)
        if value:
            return name
    return None
