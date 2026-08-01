"""Bounded, content-free Settings projection helpers."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from gigaloom.config import DEFAULT_MODEL_HINTS, HarnessConfig
from gigaloom.diagnostics.inventory.capabilities import (
    AdmissionStatus,
    legacy_mode_compatibility_receipt,
)
from gigaloom.projects.api import (
    PROJECT_STATE_FILE,
    project_config_path,
    project_id_for_root,
)
from gigaloom.settings import HarnessDefaultsSnapshot
from gigaloom.tools.mcp.api import MCPProbeHistoryStore


SETTINGS_SECTION_SCHEMA_VERSION = 1
MAX_SETTINGS_CACHE_ENTRIES = 64
MAX_SETTINGS_HARNESSES = 100
MAX_SETTINGS_MCP_SERVERS = 100
MAX_SETTINGS_MCP_ERRORS = 20
MAX_SETTINGS_MCP_HISTORY_BYTES = 256 * 1024
MAX_SETTINGS_MCP_HISTORY_ROWS = 1_000


def _defaults_projection(
    snapshot: HarnessDefaultsSnapshot,
    harnesses: tuple[Any, ...],
) -> dict[str, Any]:
    defaults = snapshot.defaults
    models = list(
        dict.fromkeys(
            model
            for model in (
                defaults.default_model,
                defaults.default_title_model,
                *DEFAULT_MODEL_HINTS,
            )
            if model
        )
    )[:20]
    return {
        "routes": {
            "default_api_mode": defaults.default_api_mode,
            "default_model": defaults.default_model,
            "default_api_mode_source": snapshot.sources["default_api_mode"],
            "default_model_source": snapshot.sources["default_model"],
            "models": models,
            "models_source": "configured_default_and_fallbacks",
            "health": "not_checked",
            "change_effect": "new_runs",
        },
        "harness_defaults": {
            **asdict(defaults),
            "harnesses": [_static_harness_projection(harness) for harness in harnesses],
            "sources": dict(snapshot.sources),
            "locked_fields": list(snapshot.locked_fields),
            "change_effect": "new_runs",
            "compatibility": {
                "mode": (
                    legacy_mode_compatibility_receipt(defaults.mode)
                    if snapshot.sources.get("task_intent") == "legacy_mode_alias"
                    or snapshot.sources.get("authority") == "legacy_mode_alias"
                    or defaults.mode not in {"plan", "read", "edit"}
                    else None
                )
            },
        },
    }


def _defaults_revision(
    snapshot: HarnessDefaultsSnapshot,
    harnesses: tuple[Any, ...],
) -> str:
    return _digest(
        {
            "settings": snapshot.revision,
            "harnesses": [
                {
                    "id": harness.spec().id,
                    "spec": _static_harness_projection(harness),
                }
                for harness in harnesses
            ],
        }
    )


def _static_harness_projection(harness: Any) -> dict[str, Any]:
    spec = harness.spec()
    capabilities = {item.value for item in tuple(spec.capabilities or ())}
    modes = []
    for mode_id, capability in (
        ("coding_agent", "agent_cli"),
        ("direct_chat", "chat_completions"),
    ):
        available = capability in capabilities
        modes.append(
            {
                "id": mode_id,
                "status": (
                    AdmissionStatus.AVAILABLE.value
                    if available
                    else AdmissionStatus.BLOCKED.value
                ),
                "why": [] if available else ["harness_capability_unavailable"],
                "recovery": [] if available else ["select_compatible_harness"],
            }
        )
    structured_default = spec.id in {"codex-cli", "claude-code", "gemini-cli"}
    terminal_ready = bool(spec.supports_native_sessions)
    return {
        "id": spec.id,
        "title": spec.title,
        "native_supported": terminal_ready,
        "status": "not_checked",
        "workbench_admission": {"schema_version": 1, "modes": modes},
        "workbench_transport": {
            "default": "native_structured" if structured_default else "one_shot",
            "options": [
                {
                    "id": "native_structured",
                    "status": "not_checked",
                    "detail": "Capability is probed only when the route is selected.",
                    "blocker": None,
                    "remediation": f"giga harness inspect {spec.id} --json",
                    "durable": True,
                    "provider_native_continuity": False,
                },
                {
                    "id": "native_terminal",
                    "status": "ready" if terminal_ready else "blocked",
                    "detail": (
                        "Managed provider CLI/TUI session; continuity is terminal-owned."
                        if terminal_ready
                        else "This adapter has no native terminal session surface."
                    ),
                    "blocker": None
                    if terminal_ready
                    else "native_terminal_unavailable",
                    "remediation": (
                        None
                        if terminal_ready
                        else f"giga harness inspect {spec.id} --json"
                    ),
                    "durable": False,
                    "provider_native_continuity": False,
                },
                {
                    "id": "one_shot",
                    "status": "ready",
                    "detail": "Compatibility execution without native continuity.",
                    "blocker": None,
                    "remediation": None,
                    "durable": False,
                    "provider_native_continuity": False,
                },
            ],
        },
    }


def _runtime_projection(config: HarnessConfig) -> dict[str, Any]:
    return {
        "proxy_url": _public_url(config.proxy_url),
        "proxy_source": (
            "environment" if os.getenv("GIGALOOM_PROXY_URL") else "built_in"
        ),
        "proxy_health": "not_checked",
        "auto_start_proxy": config.auto_start_proxy,
        "change_effect": "restart_required",
        "editable": False,
        "proxy_auth_configured": config.api_key is not None,
    }


def _runtime_revision(config: HarnessConfig) -> str:
    return _digest(_runtime_projection(config))


def _provider_summary(providers: list[dict[str, Any]]) -> dict[str, Any]:
    states = {
        item["health"]["status"] for item in providers if item.get("health") is not None
    }
    if "unhealthy" in states or "blocked" in states:
        health = "attention_required"
    elif "ready" in states:
        health = "ready"
    else:
        health = "not_checked"
    return {
        "configured": bool(providers),
        "count": len(providers),
        "source": "user_registry" if providers else "unconfigured",
        "health": health,
        "secret_readable": False,
        "change_effect": "new_session_required",
        "registry_path_readable": False,
    }


def _workspace_revision(data_dir: str | Path, workspace: str | None) -> str:
    root = _lightweight_project_root(workspace)
    project_id = project_id_for_root(root)
    state_path = (
        Path(data_dir).expanduser() / "projects" / project_id / PROJECT_STATE_FILE
    )
    return _digest(
        {
            "identity": hashlib.sha256(str(root).encode("utf-8")).hexdigest(),
            "git_marker": _path_revision(root / ".git"),
            "project_config": _path_revision(project_config_path(root)),
            "project_state": _path_revision(state_path),
        }
    )


def _workspace_id(workspace: str | None) -> str:
    """Return the stable workspace id without invoking Git or project probes."""
    return project_id_for_root(_lightweight_project_root(workspace))


def _mcp_revision(data_dir: str | Path, workspace: str | None) -> str:
    root = _lightweight_project_root(workspace)
    history = MCPProbeHistoryStore(data_dir)
    return _digest(
        {
            "workspace": _workspace_revision(data_dir, workspace),
            "project_config": _path_revision(project_config_path(root)),
            "probe_history": _path_revision(history.path),
        }
    )


def _lightweight_project_root(workspace: str | None) -> Path:
    selected = Path.cwd() if workspace is None else Path(workspace).expanduser()
    selected = selected.resolve()
    if selected.is_file():
        selected = selected.parent
    for candidate in (selected, *selected.parents):
        if (candidate / ".git").exists():
            return candidate
    return selected


def _path_revision(path: Path) -> Mapping[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        return {"exists": False}
    return {
        "ctime_ns": stat.st_ctime_ns,
        "device": stat.st_dev,
        "exists": True,
        "inode": stat.st_ino,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "type": "directory" if path.is_dir() else "file",
    }


def _bounded_mcp_health(path: Path, server_ids: set[str]) -> dict[str, str]:
    if not server_ids:
        return {}
    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            offset = max(0, size - MAX_SETTINGS_MCP_HISTORY_BYTES)
            stream.seek(offset)
            data = stream.read(MAX_SETTINGS_MCP_HISTORY_BYTES)
    except OSError:
        return {}
    if offset:
        _, _, data = data.partition(b"\n")
    health: dict[str, str] = {}
    for raw_line in reversed(data.splitlines()[-MAX_SETTINGS_MCP_HISTORY_ROWS:]):
        try:
            item = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        server_id = item.get("server_id") if isinstance(item, Mapping) else None
        if server_id in server_ids and server_id not in health:
            health[server_id] = str(item.get("status") or "not_checked")
            if len(health) == len(server_ids):
                break
    return health


def _public_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
