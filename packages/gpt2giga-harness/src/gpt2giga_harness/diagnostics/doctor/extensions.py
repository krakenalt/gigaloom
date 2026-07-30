"""Extension, bootstrap, dependency, and support readiness checks."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Mapping

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.integrations.api import (
    IntegrationCatalogStore,
    IntegrationFlowService,
)
from gpt2giga_harness.native_cli_contracts import WORKBENCH_INTEGRATION_SPECS
from gpt2giga_harness.projects.api import load_project_config, resolve_project
from gpt2giga_harness.providers.api import ProviderSettingsService
from gpt2giga_harness.runtime.api import RUNTIME_DB_NAME
from gpt2giga_harness.tools.mcp.api import build_mcp_inventory

from .models import (
    _WORKER_STALE_AFTER_SECONDS,
    _check,
    _optional_package_version,
    _remedy,
    _text_sha256,
)


def _extension_source_checks(
    config: HarnessConfig,
    workspace: str | Path | None,
) -> list[dict[str, Any]]:
    """Inspect bounded local source metadata without probing or installing."""
    requested = Path.cwd() if workspace is None else Path(workspace).expanduser()
    try:
        project = resolve_project(
            requested,
            data_dir=config.data_dir,
            load_config_name=False,
        )
        project_config = load_project_config(project.root)
        descriptors, errors = build_mcp_inventory(
            project_config.tool_profiles, project=project
        )
        enabled = sum(descriptor.enabled for descriptor in descriptors)
        mcp_check = _check(
            "mcp-sources",
            "mcp",
            "ready" if not errors else "degraded",
            (
                f"MCP sources: {len(descriptors)} configured; "
                f"{enabled} enabled; {len(errors)} invalid"
            ),
            evidence={
                "configured": len(descriptors),
                "enabled": enabled,
                "disabled": len(descriptors) - enabled,
                "invalid": len(errors),
                "probed": False,
                "tool_content_retained": False,
            },
            remediation=(
                ()
                if not errors
                else (
                    _remedy(
                        "Fix invalid project MCP descriptors, then rerun doctor.",
                        "giga doctor . --json",
                    ),
                )
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        mcp_check = _check(
            "mcp-sources",
            "mcp",
            "blocked",
            "MCP sources: project descriptors are unreadable",
            evidence={
                "error_type": type(exc).__name__,
                "error_sha256": _text_sha256(str(exc)),
                "probed": False,
            },
            remediation=(
                _remedy(
                    "Repair the project configuration before using MCP.",
                    "giga doctor . --json",
                ),
            ),
        )
    try:
        snapshot = IntegrationCatalogStore(config.data_dir).snapshot()
        source_ready = sum(item.last_attempt_succeeded for item in snapshot.sources)
        source_failed = len(snapshot.sources) - source_ready
        entry_components = {"skill": 0, "plugin": 0, "mcp": 0}
        for entry in snapshot.entries:
            package = entry.package
            for component in () if package is None else package.components:
                kind = str(component.type.value)
                if kind in entry_components:
                    entry_components[kind] += 1
    except (OSError, RuntimeError, ValueError) as exc:
        catalog_error = {
            "error_type": type(exc).__name__,
            "error_sha256": _text_sha256(str(exc)),
        }
        source_ready = 0
        source_failed = 1
        entry_components = {"skill": 0, "plugin": 0, "mcp": 0}
    else:
        catalog_error = {}
    skills_proxy_configured = bool(os.environ.get("GIGA_SKILLS_PROXY_ORIGIN"))
    skills_check = _check(
        "skills-sources",
        "skills",
        "ready" if skills_proxy_configured and source_failed == 0 else "degraded",
        (
            "Skills sources: local metadata available; skills.sh proxy configured"
            if skills_proxy_configured
            else "Skills sources: skills.sh request-scoped OIDC proxy is not configured"
        ),
        evidence={
            "cached_entries": entry_components["skill"],
            "catalog_sources_ready": source_ready,
            "catalog_sources_unavailable": source_failed,
            "skills_sh_proxy_configured": skills_proxy_configured,
            "skills_sh_oidc_ownership": "request_scoped_proxy",
            "oidc_material_readable": False,
            **catalog_error,
        },
        remediation=(
            ()
            if skills_proxy_configured and source_failed == 0
            else (
                _remedy(
                    "Start the read-only skills.sh proxy with a request-scoped OIDC token.",
                    "giga-skills-catalog-proxy",
                ),
                _remedy(
                    "Point GigaLoom at the loopback catalog proxy.",
                    "GIGA_SKILLS_PROXY_ORIGIN=http://127.0.0.1:8092 giga ui",
                ),
            )
        ),
    )
    plugins_check = _check(
        "plugin-sources",
        "plugins",
        "ready" if source_failed == 0 else "degraded",
        "Plugin sources: governed local and cached catalog metadata is readable",
        evidence={
            "cached_entries": entry_components["plugin"],
            "catalog_sources_ready": source_ready,
            "catalog_sources_unavailable": source_failed,
            "root_metadata_scan": "not_checked",
            "plugin_content_retained": False,
            **catalog_error,
        },
        remediation=(
            ()
            if source_failed == 0
            else (
                _remedy(
                    "Inspect cached integration source health.",
                    "giga integration list --json",
                ),
            )
        ),
    )
    return [mcp_check, skills_check, plugins_check]


def _bootstrap_discovery_checks(config: HarnessConfig) -> list[dict[str, Any]]:
    """Return bounded local discovery used by doctor and reviewed bootstrap."""
    checks = [
        _github_cli_check(),
        _optional_dependencies_check(),
        _support_export_check(),
    ]
    try:
        providers = ProviderSettingsService(str(config.data_dir)).list()
        configured = providers.get("providers") or []
        ownership_counts: dict[str, int] = {}
        for provider in configured:
            if not isinstance(provider, Mapping):
                continue
            authentication = provider.get("authentication")
            if not isinstance(authentication, Mapping):
                continue
            ownership = str(authentication.get("ownership") or "unknown")
            ownership_counts[ownership] = ownership_counts.get(ownership, 0) + 1
        checks.append(
            _check(
                "provider-profiles",
                "bootstrap",
                "ready",
                (
                    f"Provider profiles: {len(configured)} configured; "
                    f"{len(providers.get('templates') or ())} templates"
                ),
                evidence={
                    "configured": len(configured),
                    "templates": len(providers.get("templates") or ()),
                    "authentication_ownership": ownership_counts,
                    "native_cli_authentication": {
                        namespace: {
                            "ownership": "provider_native",
                            "status": "not_checked",
                        }
                        for namespace in sorted(WORKBENCH_INTEGRATION_SPECS)
                    },
                    "values_resolved": False,
                },
                remediation=(
                    _remedy(
                        "Review provider profiles and reference-only authentication.",
                        "giga provider list --json",
                    ),
                ),
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        checks.append(
            _check(
                "provider-profiles",
                "bootstrap",
                "degraded",
                "Provider profiles: local registry is unreadable",
                evidence={"error": str(exc), "values_resolved": False},
                remediation=(
                    _remedy(
                        "Inspect the local provider registry without resolving secrets.",
                        "giga provider list --json",
                    ),
                ),
            )
        )
    try:
        catalog = IntegrationCatalogStore(config.data_dir).list()
        flows = IntegrationFlowService(config.data_dir).list()
        statuses: dict[str, int] = {}
        for flow in flows:
            status = flow.status.value
            statuses[status] = statuses.get(status, 0) + 1
        checks.append(
            _check(
                "extensions",
                "bootstrap",
                "ready",
                (
                    f"Extensions: {len(catalog)} cached catalog entries; "
                    f"{len(flows)} retained flow(s)"
                ),
                evidence={
                    "catalog_entries": len(catalog),
                    "retained_flows": len(flows),
                    "flow_statuses": statuses,
                    "installation_authorized": False,
                },
                remediation=(
                    _remedy(
                        "Review extension compatibility and retained flow state.",
                        "giga integration list --json",
                    ),
                ),
            )
        )
    except (OSError, ValueError) as exc:
        checks.append(
            _check(
                "extensions",
                "bootstrap",
                "degraded",
                "Extensions: local catalog or flow state is unreadable",
                evidence={"error": str(exc), "installation_authorized": False},
                remediation=(
                    _remedy(
                        "Inspect local integration state before any setup action.",
                        "giga integration list --json",
                    ),
                ),
            )
        )
    return checks


def _github_cli_check() -> dict[str, Any]:
    executable = shutil.which("gh")
    return _check(
        "github-cli",
        "bootstrap",
        "ready" if executable else "degraded",
        (
            "GitHub CLI: installed; authentication not checked"
            if executable
            else "GitHub CLI: not installed"
        ),
        evidence={
            "installed": executable is not None,
            "authentication_status": "not_checked",
            "network_contacted": False,
        },
        remediation=(
            _remedy(
                (
                    "Inspect local GitHub authentication explicitly."
                    if executable
                    else "Install GitHub CLI, then authenticate explicitly."
                ),
                "gh auth status" if executable else "giga doctor --json",
            ),
        ),
    )


def _optional_dependencies_check() -> dict[str, Any]:
    packages = {
        name: _optional_package_version(name)
        for name in ("claude-agent-sdk", "gigachat", "gpt2giga")
    }
    available = sum(value != "unknown" for value in packages.values())
    return _check(
        "optional-dependencies",
        "bootstrap",
        "ready",
        f"Optional dependencies: {available}/{len(packages)} installed",
        evidence={
            "packages": packages,
            "required_for_base_install": False,
        },
        remediation=(
            _remedy(
                "Install only the reviewed optional capability you intend to use.",
                "giga doctor --json",
            ),
        ),
    )


def _support_export_check() -> dict[str, Any]:
    return _check(
        "support-export",
        "bootstrap",
        "ready",
        "Support export: private canonical JSON is available",
        evidence={"content_free": True, "mode": "0600", "atomic": True},
        remediation=(
            _remedy(
                "Export the current redacted report for support.",
                "giga doctor --json --output doctor-support.json",
            ),
        ),
    )


def _read_worker_state(data_dir: str | Path) -> dict[str, Any]:
    path = Path(data_dir).expanduser() / RUNTIME_DB_NAME
    if not path.is_file():
        return {"initialized": False, "online": 0, "offline": 0, "total": 0}
    try:
        with closing(
            sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(
                "SELECT status, heartbeat_at FROM workers"
            ).fetchall()
    except (OSError, sqlite3.Error):
        return {
            "initialized": True,
            "online": 0,
            "offline": 0,
            "total": 0,
            "readable": False,
        }
    now = datetime.now(timezone.utc).timestamp()
    online = 0
    for status, heartbeat_at in rows:
        try:
            heartbeat = datetime.fromisoformat(str(heartbeat_at)).timestamp()
        except ValueError:
            heartbeat = 0.0
        if status == "online" and now - heartbeat <= _WORKER_STALE_AFTER_SECONDS:
            online += 1
    return {
        "initialized": True,
        "readable": True,
        "online": online,
        "offline": len(rows) - online,
        "total": len(rows),
    }


def _path_can_be_created(path: Path) -> bool:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)
