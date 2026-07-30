"""Provider, proxy, and Harness readiness checks."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness import proxy
from gpt2giga_harness.cli_capabilities import cli_capability_snapshot_to_dict
from gpt2giga_harness.config import (
    HarnessConfig,
    pass_model_env_note,
)
from gpt2giga_harness.native_cli_contracts import WORKBENCH_INTEGRATION_SPECS
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.types import AvailabilityStatus

from .models import (
    _check,
    _credentials_source,
    _health_text,
    _remedy,
    _route_probe_text,
    _sidecar_text,
    _text_sha256,
)


def _proxy_checks(
    config: HarnessConfig,
    health: proxy.ProxyHealth,
    sidecar: proxy.SidecarPreflight,
    models: proxy.ModelDiscovery,
    route_probes: Mapping[str, proxy.RouteProbe],
    *,
    selected_api_mode: str | None = None,
    route_paths: tuple[str, ...] = ("/v1/chat/completions", "/v2/chat/completions"),
    selected_model: str | None = None,
) -> list[dict[str, Any]]:
    if health.ok:
        proxy_status = "ready"
        proxy_remediation: tuple[dict[str, str], ...] = ()
    elif sidecar.ok:
        proxy_status = "degraded"
        proxy_remediation = (_remedy("Start the configured local proxy.", "gpt2giga"),)
    else:
        proxy_status = "blocked"
        proxy_remediation = (
            _remedy(
                "Configure proxy access or fix local auto-start prerequisites.",
                "giga doctor --json",
            ),
        )
    sidecar_ready = sidecar.ok or health.ok
    sidecar_text = (
        _sidecar_text(sidecar)
        if sidecar.ok or not health.ok
        else "not needed; proxy already reachable"
    )
    checks = [
        _check(
            "proxy-health",
            "proxy",
            proxy_status,
            f"Proxy / Health: {_health_text(health)}",
            evidence={
                "configured_url": config.proxy_url,
                "reachable": health.ok,
                "health_path": health.path,
                "status_code": health.status_code,
                "error": health.error,
            },
            remediation=proxy_remediation,
        ),
        _check(
            "proxy-autostart",
            "proxy",
            "ready" if sidecar_ready else "degraded",
            f"Proxy / Auto-start: {sidecar_text}",
            evidence={"ready": sidecar.ok, "reason": sidecar.reason},
            remediation=(
                ()
                if sidecar_ready
                else (
                    _remedy(
                        "Configure local GigaChat access or use an existing proxy.",
                        "giga doctor --no-start-proxy --json",
                    ),
                )
            ),
        ),
    ]
    selected_mode = selected_api_mode or config.default_api_mode.value
    for path in route_paths:
        route = route_probes.get(path)
        status = (
            "ready"
            if route is not None and route.ok
            else "degraded"
            if route is None and sidecar.ok
            else "blocked"
            if path.split("/")[1] == selected_mode
            else "degraded"
        )
        checks.append(
            _check(
                f"route-{path.split('/')[1]}",
                "routes",
                status,
                f"{path}: {_route_probe_text(route)}",
                evidence=(
                    {"reachable": False, "status_code": None}
                    if route is None
                    else {
                        "reachable": route.ok,
                        "status_code": route.status_code,
                        "detail": route.detail,
                    }
                ),
                remediation=(
                    ()
                    if status == "ready"
                    else (
                        _remedy(
                            "Start a compatible gateway and verify the selected route.",
                            "giga doctor --json",
                        ),
                    )
                ),
            )
        )
    model_status = (
        "ready"
        if models.models or selected_model or config.default_model
        else "degraded"
    )
    checks.append(
        _check(
            "model-discovery",
            "routes",
            model_status,
            f"Models: {len(models.models)} candidate(s) from {models.source}",
            evidence={
                "count": len(models.models),
                "source": models.source,
                "default_configured": bool(selected_model or config.default_model),
            },
            remediation=(
                ()
                if model_status == "ready"
                else (
                    _remedy(
                        "Set a default model or fix proxy model discovery.",
                        "export GPT2GIGA_HARNESS_DEFAULT_MODEL=<model-from-/v2/models>",
                    ),
                )
            ),
        )
    )
    return checks


def _offline_proxy_checks(
    sidecar: proxy.SidecarPreflight,
) -> list[dict[str, Any]]:
    """Describe proxy readiness without model discovery or chat traffic."""
    checks = [
        _check(
            "proxy-health",
            "proxy",
            "degraded",
            "Proxy / Health: not checked by the privacy-safe Web doctor",
            evidence={"checked": False, "network_contacted": False},
            remediation=(
                _remedy(
                    "Run the explicit CLI doctor when live route checks are intended.",
                    "giga doctor --json",
                ),
            ),
        ),
        _check(
            "proxy-autostart",
            "proxy",
            "ready" if sidecar.ok else "degraded",
            (
                "Proxy / Auto-start: local prerequisites are ready"
                if sidecar.ok
                else "Proxy / Auto-start: local prerequisites are incomplete"
            ),
            evidence={
                "ready": sidecar.ok,
                "reason_sha256": (None if sidecar.ok else _text_sha256(sidecar.reason)),
                "network_contacted": False,
            },
            remediation=(
                ()
                if sidecar.ok
                else (
                    _remedy(
                        "Configure local proxy prerequisites, then rerun doctor.",
                        "giga doctor --no-start-proxy --json",
                    ),
                )
            ),
        ),
    ]
    checks.extend(
        _check(
            f"route-{api_mode}",
            "routes",
            "degraded",
            f"/{api_mode}/chat/completions: not checked",
            evidence={"checked": False, "network_contacted": False},
            remediation=(
                _remedy(
                    "Run the explicit CLI doctor to check this route.",
                    "giga doctor --json",
                ),
            ),
        )
        for api_mode in ("v1", "v2")
    )
    checks.append(
        _check(
            "model-discovery",
            "routes",
            "degraded",
            "Models: discovery not checked",
            evidence={"checked": False, "network_contacted": False},
            remediation=(
                _remedy(
                    "Use the explicit model discovery action when provider traffic is intended.",
                    "giga provider list --json",
                ),
            ),
        )
    )
    return checks


def _gigachat_check(
    config: HarnessConfig,
    health: proxy.ProxyHealth,
) -> dict[str, Any]:
    source = _credentials_source()
    configured = source is not None
    status = "ready" if configured or health.ok else "degraded"
    model = config.default_model or "not configured"
    api_mode_env = os.getenv("GPT2GIGA_GIGACHAT_API_MODE") or "not set"
    pass_model = pass_model_env_note() or "not set"
    summary = (
        "GigaChat / Upstream access: "
        f"{'configured (redacted)' if configured else 'not configured'}; "
        f"default model: {model}"
    )
    return _check(
        "gigachat-upstream",
        "gigachat",
        status,
        summary,
        evidence={
            "configured": configured,
            "source_env": source,
            "api_mode_env": api_mode_env,
            "pass_model": pass_model,
            "running_proxy_reachable": health.ok,
        },
        remediation=(
            ()
            if status == "ready"
            else (
                _remedy(
                    "Configure GigaChat credentials for local proxy auto-start.",
                    "giga doctor --json",
                ),
            )
        ),
    )


def _harness_checks(
    registry: HarnessRegistry,
    *,
    harness_ids: tuple[str, ...] | None = None,
    include_compatibility: bool = True,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for harness in registry.list():
        spec = harness.spec()
        if harness_ids is not None and spec.id not in harness_ids:
            continue
        availability = harness.availability()
        status = (
            "ready"
            if availability.status is AvailabilityStatus.AVAILABLE
            else "degraded"
        )
        evidence: dict[str, Any] = {
            "availability": availability.status.value,
            "reason_sha256": (
                _text_sha256(availability.reason) if availability.reason else None
            ),
        }
        probe_method = getattr(harness, "capability_probe", None)
        if include_compatibility and callable(probe_method):
            probe = probe_method()
            compatibility = cli_capability_snapshot_to_dict(probe)
            compatibility.pop("command", None)
            evidence["compatibility"] = compatibility
            namespace = _native_namespace_for_harness(spec.id)
            if namespace is not None:
                resolution_method = getattr(harness, "executable_resolution", None)
                resolution = (
                    resolution_method() if callable(resolution_method) else None
                )
                evidence["native_facade"] = _native_facade_evidence(
                    namespace,
                    probe_status=probe.status,
                    compatible=probe.compatible,
                    version=probe.parsed_version or probe.version,
                    version_status=probe.version_window_status,
                    executable=(
                        resolution.executable if resolution is not None else None
                    ),
                    executable_source=(
                        resolution.source if resolution is not None else "unknown"
                    ),
                )
        checks.append(
            _check(
                f"harness-{spec.id}",
                "harnesses",
                status,
                f"Harness / {spec.id}: {availability.status.value}",
                evidence=evidence,
                remediation=(
                    ()
                    if status == "ready"
                    else (
                        _remedy(
                            "Install or configure the adapter executable, then inspect it.",
                            f"giga harness inspect {spec.id} --json",
                        ),
                    )
                ),
            )
        )
    return checks


def _native_namespace_for_harness(harness_id: str) -> str | None:
    return {
        "codex-cli": "codex",
        "claude-code": "claude",
        "gemini-cli": "gemini",
    }.get(harness_id)


def _native_facade_evidence(
    namespace: str,
    *,
    probe_status: str,
    compatible: bool,
    version: str | None,
    version_status: str,
    executable: str | None,
    executable_source: str,
) -> dict[str, Any]:
    integration = WORKBENCH_INTEGRATION_SPECS[namespace]
    executable_ready = executable is not None and probe_status != "missing"
    l2_ready = (
        executable_ready and compatible and bool(integration.structured_transport)
    )
    degradation = None
    remediation = None
    if not executable_ready:
        degradation = "native_runtime_missing"
        remediation = (
            f"Install {namespace} on PATH or configure executables."
            f"{integration.harness_id}."
        )
    elif not l2_ready:
        degradation = (
            "provider_owned_l1"
            if integration.structured_transport is None
            else f"structured_{version_status}"
        )
        remediation = (
            "Use the visible provider-owned L1 handoff, or install a reviewed "
            "structured-transport version; L0 remains available."
        )
    return {
        "namespace": namespace,
        "executable": Path(executable).name if executable is not None else None,
        "executable_present": executable is not None,
        "executable_source": executable_source,
        "version": version,
        "levels": {
            "L0": "ready" if executable_ready else "blocked",
            "L1": "ready" if executable_ready else "blocked",
            "L2": "ready"
            if l2_ready
            else "degraded"
            if executable_ready
            else "blocked",
        },
        "transport": integration.structured_transport,
        "fallback": integration.l1_fallback,
        "degradation": degradation,
        "remediation": remediation,
    }
