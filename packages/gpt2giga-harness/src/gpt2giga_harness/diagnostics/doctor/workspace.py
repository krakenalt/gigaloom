"""Workspace, UI identity, worker, and managed-home readiness checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gpt2giga_harness.config import HarnessConfig
from gpt2giga_harness.projects.api import load_project_config, resolve_project
from gpt2giga_harness.runtime.network_access import network_access_manifest
from gpt2giga_harness.tools.mcp.api import HeadlessManagedMCPSnapshotStore

from .extensions import _path_can_be_created, _read_worker_state
from .models import (
    _MAX_SNAPSHOT_BYTES,
    _MAX_SNAPSHOT_VALIDATIONS,
    _check,
    _is_loopback_host,
    _remedy,
    _safe_enum,
    _text_sha256,
)


def _workspace_checks(
    config: HarnessConfig,
    workspace: str | Path | None,
) -> list[dict[str, Any]]:
    requested = Path.cwd() if workspace is None else Path(workspace).expanduser()
    if not requested.exists() or not requested.is_dir():
        return [
            _check(
                "workspace",
                "workspace",
                "blocked",
                "Workspace: directory is missing or unreadable",
                evidence={"exists": requested.exists(), "is_directory": False},
                remediation=(
                    _remedy("Choose an existing project directory.", "giga doctor ."),
                ),
            )
        ]
    try:
        project = resolve_project(
            requested,
            data_dir=config.data_dir,
            load_config_name=False,
        )
        project_config = load_project_config(project.root)
    except (OSError, RuntimeError, ValueError) as exc:
        return [
            _check(
                "workspace",
                "workspace",
                "blocked",
                "Workspace: Harness project configuration is invalid",
                evidence={"error": str(exc)},
                remediation=(
                    _remedy(
                        "Fix the redacted project configuration error.", "giga init"
                    ),
                ),
            )
        ]
    dirty = dict(project.dirty_summary)
    git_status = "ready" if project.is_git_repo else "degraded"
    config_status = "ready" if project_config.exists else "degraded"
    return [
        _check(
            "workspace",
            "workspace",
            "ready",
            "Workspace: ready",
            evidence={
                "exists": True,
                "project_id": project.id,
            },
        ),
        _check(
            "git-readiness",
            "workspace",
            git_status,
            (
                "Git: repository ready"
                if project.is_git_repo
                else "Git: current workspace is not a repository"
            ),
            evidence={
                "is_repository": project.is_git_repo,
                "branch_present": bool(project.git_branch),
                "dirty_counts": dirty,
            },
            remediation=(
                ()
                if git_status == "ready"
                else (
                    _remedy("Initialize Git for reviewed worktree flows.", "git init"),
                )
            ),
        ),
        _check(
            "project-config",
            "workspace",
            config_status,
            (
                "Project config: .giga/harness.toml is ready"
                if project_config.exists
                else "Project config: not initialized"
            ),
            evidence={"configured": project_config.exists},
            remediation=(
                ()
                if config_status == "ready"
                else (
                    _remedy(
                        "Create safe starter project configuration.",
                        "giga init",
                    ),
                )
            ),
        ),
    ]


def _ui_identity_check(
    config: HarnessConfig,
    current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project local or remote UI identity without cookies or principal data."""
    if current is not None:
        local = bool(current.get("local"))
        authenticated = bool(current.get("authenticated"))
        status = "ready" if authenticated else "blocked"
        return _check(
            "ui-identity",
            "ui_identity",
            status,
            (
                "UI identity: current local browser session is active"
                if local and authenticated
                else "UI identity: current remote browser session is active"
                if authenticated
                else "UI identity: current browser session is unavailable"
            ),
            evidence={
                "mode": "local" if local else "remote",
                "authenticated": authenticated,
                "claimable": bool(current.get("claimable")),
                "role": _safe_enum(current.get("role"), {"viewer", "operator"}),
                "session_content_retained": False,
            },
            remediation=(
                ()
                if authenticated
                else (
                    _remedy(
                        "Recover the current UI identity boundary.",
                        "giga ui" if local else "giga ui-identity validate --json",
                    ),
                )
            ),
        )
    if _is_loopback_host(config.ui_host):
        try:
            from gpt2giga_harness.ui.local_access import LocalUIAccessStore

            local_status = LocalUIAccessStore(config.data_dir).status()
        except (OSError, RuntimeError, ValueError) as exc:
            return _check(
                "ui-identity",
                "ui_identity",
                "blocked",
                "UI identity: local access state is unreadable",
                evidence={
                    "mode": "local",
                    "error_type": type(exc).__name__,
                    "error_sha256": _text_sha256(str(exc)),
                },
                remediation=(_remedy("Recover local UI access.", "giga ui"),),
            )
        return _check(
            "ui-identity",
            "ui_identity",
            "ready",
            "UI identity: private loopback access state is readable",
            evidence={
                "mode": "local",
                "claimable": local_status.claimable,
                "current_browser_session": "not_checked",
                "session_content_retained": False,
            },
            remediation=(
                _remedy(
                    "Open the UI to claim or inspect the current browser session.",
                    "giga ui",
                ),
            ),
        )
    try:
        from gpt2giga_harness.ui.remote_identity import RemoteOIDCSettings

        settings = RemoteOIDCSettings.from_config(config)
    except (RuntimeError, ValueError) as exc:
        return _check(
            "ui-identity",
            "ui_identity",
            "blocked",
            "UI identity: remote OIDC configuration is incomplete or invalid",
            evidence={
                "mode": "remote",
                "error_type": type(exc).__name__,
                "error_sha256": _text_sha256(str(exc)),
            },
            remediation=(
                _remedy(
                    "Validate the deployment-owned remote identity profile.",
                    "giga ui-identity validate --json",
                ),
            ),
        )
    return _check(
        "ui-identity",
        "ui_identity",
        "ready",
        "UI identity: remote OIDC boundary is configured",
        evidence={
            "mode": "remote",
            "role_mapping_count": len(settings.roles),
            "trusted_proxy_count": len(settings.trusted_proxies),
            "current_browser_session": "not_checked",
            "issuer_content_retained": False,
        },
        remediation=(
            _remedy(
                "Validate remote identity without starting a listener.",
                "giga ui-identity validate --json",
            ),
        ),
    )


def _network_availability_check() -> dict[str, Any]:
    manifest = network_access_manifest()
    return _check(
        "scoped-network",
        "network",
        "ready",
        "Network: scoped HTTPS authority is available and defaults to deny",
        evidence={
            "schema_version": manifest["schema_version"],
            "default": manifest["default_sandbox_network_access"],
            "reviewed_proxy_optional": manifest["reviewed_proxy"]["optional"],
            "blanket_internet_switch": manifest["blanket_internet_switch"],
            "live_network_side_effect": False,
        },
        remediation=(
            _remedy(
                "Preview the exact network action and approve only its bounded grant.",
                "giga doctor --json",
            ),
        ),
    )


def _worker_check(config: HarnessConfig) -> dict[str, Any]:
    state = _read_worker_state(config.data_dir)
    readable = state.get("readable", True)
    status = "ready" if state["online"] else "degraded" if readable else "blocked"
    return _check(
        "durable-worker",
        "worker",
        status,
        f"Durable worker: {state['online']} online; {state['total']} recorded",
        evidence=state,
        remediation=(
            ()
            if status == "ready"
            else (
                _remedy(
                    (
                        "Start a durable Harness worker."
                        if readable
                        else "Inspect the unreadable runtime coordination store."
                    ),
                    "giga worker start" if readable else "giga runtime inspect --json",
                ),
            )
        ),
    )


def _managed_homes_check(config: HarnessConfig) -> dict[str, Any]:
    data_dir = Path(config.data_dir).expanduser()
    writable = _path_can_be_created(data_dir)
    homes_root = data_dir / "native"
    home_count = (
        sum(1 for path in homes_root.glob("*/homes/*") if path.is_dir())
        if homes_root.exists()
        else 0
    )
    status = "ready" if writable else "blocked"
    return _check(
        "managed-homes",
        "managed-state",
        status,
        f"Managed homes: storage {'ready' if writable else 'not writable'}; {home_count} home(s)",
        evidence={
            "storage_initialized": data_dir.exists(),
            "storage_writable": writable,
            "home_count": home_count,
        },
        remediation=(
            ()
            if status == "ready"
            else (
                _remedy(
                    "Choose a writable Harness data directory.",
                    "export GPT2GIGA_HARNESS_DATA_DIR=/path/to/writable/state",
                ),
            )
        ),
    )


def _managed_mcp_check(config: HarnessConfig) -> dict[str, Any]:
    data_dir = Path(config.data_dir).expanduser()
    root = data_dir / "tools" / "headless_mcp_snapshots"
    paths = sorted(root.glob("*.json")) if root.exists() else []
    invalid = 0
    checked = 0
    store = HeadlessManagedMCPSnapshotStore(data_dir)
    for path in paths[:_MAX_SNAPSHOT_VALIDATIONS]:
        checked += 1
        try:
            if path.stat().st_size > _MAX_SNAPSHOT_BYTES:
                raise ValueError("snapshot is too large")
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, Mapping):
                raise ValueError("snapshot must be an object")
            store.load(
                {
                    "snapshot_id": record.get("snapshot_id"),
                    "snapshot_hash": record.get("snapshot_hash"),
                    "project_id": record.get("project_id"),
                    "harness_id": record.get("harness_id"),
                }
            )
        except (OSError, ValueError, json.JSONDecodeError):
            invalid += 1
    skipped = max(0, len(paths) - checked)
    status = "ready" if invalid == 0 and skipped == 0 else "degraded"
    return _check(
        "managed-mcp-snapshots",
        "managed-state",
        status,
        f"Managed MCP snapshots: {len(paths)} stored; {invalid} invalid; {skipped} unchecked",
        evidence={
            "stored": len(paths),
            "validated": checked,
            "invalid": invalid,
            "unchecked": skipped,
        },
        remediation=(
            ()
            if status == "ready"
            else (
                _remedy(
                    "Inspect configured MCP profiles and recreate invalid snapshots.",
                    "giga doctor --json",
                ),
            )
        ),
    )
