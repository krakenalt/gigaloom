"""Doctor report composition and terminal presentation."""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness import proxy
from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.diagnostics.inventory.product import product_inventory_summary
from gpt2giga_harness.registry import HarnessRegistry, create_default_registry

from .checks import (
    _gigachat_check,
    _harness_checks,
    _native_facade_evidence,
    _offline_proxy_checks,
    _proxy_checks,
)
from .export import write_doctor_support_report
from .extensions import _bootstrap_discovery_checks, _extension_source_checks
from .models import (
    DOCTOR_REPORT_KIND,
    DOCTOR_SCHEMA_VERSION,
    _GUIDED_DOMAINS,
    _MAX_DOCTOR_CHECKS,
    _chat_route_probes,
    _check,
    _disabled_actions,
    _json_sha256,
    _package_version,
    _remedy,
    _route_probe_model,
    _sanitize_report,
    _text_sha256,
)
from .workspace import (
    _managed_homes_check,
    _managed_mcp_check,
    _network_availability_check,
    _ui_identity_check,
    _worker_check,
    _workspace_checks,
)

__all__ = [
    "_gigachat_check",
    "_harness_checks",
    "_managed_homes_check",
    "_native_facade_evidence",
    "_proxy_checks",
    "_sanitize_report",
    "_worker_check",
    "_workspace_checks",
    "build_doctor_report",
    "format_doctor_report",
    "run_doctor",
    "write_doctor_support_report",
]


def build_doctor_report(
    config: HarnessConfig,
    registry: HarnessRegistry | None = None,
    *,
    workspace: str | Path | None = None,
    online_checks: bool = True,
    ui_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redaction-safe, machine-readable first-run readiness report."""
    registry = registry or create_default_registry()
    sidecar = proxy.sidecar_preflight(config.to_context())
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "runtime",
            "runtime",
            "ready",
            f"Runtime / Python: {platform.python_version()}; package import: OK",
            evidence={"python": platform.python_version(), "package_import": True},
        )
    )
    if online_checks:
        health = proxy.health_check(config)
        models = proxy.discover_models(config, config.default_api_mode)
        route_probes = (
            _chat_route_probes(config, _route_probe_model(config, models))
            if health.ok
            else {}
        )
        checks.extend(_proxy_checks(config, health, sidecar, models, route_probes))
    else:
        health = proxy.ProxyHealth(
            ok=False,
            url=config.proxy_url,
            error="online checks disabled",
        )
        checks.extend(_offline_proxy_checks(sidecar))
    checks.append(_gigachat_check(config, health))
    checks.extend(_harness_checks(registry))
    checks.extend(_workspace_checks(config, workspace))
    checks.append(_ui_identity_check(config, ui_identity))
    checks.append(_worker_check(config))
    checks.append(_network_availability_check())
    checks.append(_managed_homes_check(config))
    checks.append(_managed_mcp_check(config))
    checks.extend(_extension_source_checks(config, workspace))
    checks.extend(_bootstrap_discovery_checks(config))
    if registry.discovery_errors:
        checks.append(
            _check(
                "plugin-discovery",
                "harnesses",
                "degraded",
                f"Harness plugins: {len(registry.discovery_errors)} discovery error(s)",
                evidence={
                    "error_count": len(registry.discovery_errors),
                    "error_sha256": [
                        _text_sha256(error) for error in registry.discovery_errors[:20]
                    ],
                },
                remediation=(
                    _remedy(
                        "Inspect or remove the failing Harness plugin.",
                        "giga harness list --json",
                    ),
                ),
            )
        )
    checks = checks[:_MAX_DOCTOR_CHECKS]
    summary = {
        status: sum(check["status"] == status for check in checks)
        for status in ("ready", "degraded", "blocked")
    }
    report: dict[str, Any] = {
        "schema_version": DOCTOR_SCHEMA_VERSION,
        "kind": DOCTOR_REPORT_KIND,
        "guided": {
            "first_run": True,
            "online_checks": online_checks,
            "domains": list(_GUIDED_DOMAINS),
            "disabled_actions": _disabled_actions(checks),
        },
        "privacy": {
            "content_free": True,
            "prompts_collected": False,
            "sensitive_values_collected": False,
            "oauth_material_collected": False,
            "raw_traffic_collected": False,
            "private_file_content_collected": False,
            "raw_paths_collected": False,
        },
        "environment": {
            "packages": {
                "gpt2giga": _package_version("gpt2giga"),
                "gigaloom": _package_version("gigaloom"),
            },
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
        },
        "product_inventory": product_inventory_summary(),
        "ok": summary["blocked"] == 0,
        "summary": summary,
        "checks": checks,
    }
    report["export"] = {
        "format": "canonical_json",
        "private_mode": "0600",
        "check_count": len(checks),
        "max_check_count": _MAX_DOCTOR_CHECKS,
        "content_sha256": _json_sha256(report),
    }
    return dict(_sanitize_report(report))


def run_doctor(
    config: HarnessConfig,
    registry: HarnessRegistry | None = None,
    *,
    workspace: str | Path | None = None,
) -> str:
    """Build a human-readable diagnostic report without printing secrets."""
    return format_doctor_report(
        build_doctor_report(config, registry, workspace=workspace)
    )


def format_doctor_report(report: Mapping[str, Any]) -> str:
    """Format one structured doctor report for terminal users."""
    summary = report.get("summary") or {}
    lines = [
        "gpt2giga Unified Harness doctor",
        (
            "Summary: "
            f"{summary.get('ready', 0)} ready, "
            f"{summary.get('degraded', 0)} degraded, "
            f"{summary.get('blocked', 0)} blocked"
        ),
    ]
    for raw_check in report.get("checks") or ():
        if not isinstance(raw_check, Mapping):
            continue
        status = str(raw_check.get("status") or "unknown").upper()
        lines.extend(["", f"[{status}] {raw_check.get('summary') or 'Unknown check'}"])
        native_facade = (raw_check.get("evidence") or {}).get("native_facade")
        if isinstance(native_facade, Mapping):
            levels = native_facade.get("levels") or {}
            lines.append(
                "  Native facade: "
                f"{native_facade.get('namespace')}; "
                f"executable={native_facade.get('executable') or 'missing'}; "
                f"version={native_facade.get('version') or 'unknown'}; "
                f"L0={levels.get('L0', 'unknown')}; "
                f"L1={levels.get('L1', 'unknown')}; "
                f"L2={levels.get('L2', 'unknown')}; "
                f"transport={native_facade.get('transport') or 'none'}; "
                f"fallback={native_facade.get('fallback') or 'none'}"
            )
            if native_facade.get("degradation"):
                lines.append(f"  Degradation: {native_facade['degradation']}")
            if native_facade.get("remediation"):
                lines.append(f"  Remedy: {native_facade['remediation']}")
        for remediation in raw_check.get("remediation") or ():
            if not isinstance(remediation, Mapping):
                continue
            message = remediation.get("message")
            command = remediation.get("command")
            if message:
                lines.append(f"  Remedy: {message}")
            if command:
                lines.append(f"  Command: {command}")
    return "\n".join(lines)
