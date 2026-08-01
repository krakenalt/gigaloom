"""Source-bound, probe-free Settings section projections."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from typing import Any, Callable

from gigaloom.config import HarnessConfig
from gigaloom.projects.api import (
    load_project_config,
    load_project_state,
    resolve_project,
)
from gigaloom.settings import HarnessDefaultsSnapshot, HarnessSettingsStore
from gigaloom.tools.mcp.api import MCPProbeHistoryStore, build_mcp_inventory

from .settings_projections import (
    MAX_SETTINGS_CACHE_ENTRIES,
    MAX_SETTINGS_HARNESSES,
    MAX_SETTINGS_MCP_ERRORS,
    MAX_SETTINGS_MCP_HISTORY_BYTES,
    MAX_SETTINGS_MCP_SERVERS,
    SETTINGS_SECTION_SCHEMA_VERSION,
    _bounded_mcp_health,
    _defaults_projection,
    _defaults_revision,
    _digest,
    _mcp_revision,
    _provider_summary,
    _runtime_projection,
    _runtime_revision,
    _workspace_revision,
)


class SettingsSnapshotService:
    """Build bounded Settings projections keyed by observable source revisions."""

    def __init__(
        self,
        *,
        config: HarnessConfig,
        settings_store: HarnessSettingsStore,
        provider_settings_service: Any,
        registry: Any,
        async_diagnostics: Any,
        cache_entries: int = MAX_SETTINGS_CACHE_ENTRIES,
    ) -> None:
        if cache_entries < 1 or cache_entries > MAX_SETTINGS_CACHE_ENTRIES:
            raise ValueError("Settings cache_entries is outside the accepted bound")
        self._config = config
        self._settings_store = settings_store
        self._provider_settings_service = provider_settings_service
        self._registry = registry
        self._async_diagnostics = async_diagnostics
        self._cache_entries = cache_entries
        self._cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._lock = RLock()

    def summary(self, workspace: str | None) -> dict[str, Any]:
        """Return lightweight source revisions without project or process probes."""
        snapshot = self._settings_store.load()
        providers = self._provider_settings_service.list()
        revisions = {
            "runtime": self._runtime_revision(),
            "defaults": self._defaults_revision(snapshot),
            "workspace": self._workspace_revision(workspace),
            "mcp": self._mcp_revision(workspace),
            "diagnostics": "live",
            "providers": _digest(providers),
            "provider_accounts": "explicit_process_read",
        }
        revision = _digest(revisions)

        def build() -> dict[str, Any]:
            return {
                "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
                "revision": revision,
                "sections": {
                    section: {
                        "href": f"/api/settings/{section}",
                        "revision": section_revision,
                        "cacheable": section_revision
                        not in {
                            "live",
                            "explicit_process_read",
                        },
                    }
                    for section, section_revision in revisions.items()
                    if section not in {"providers", "provider_accounts"}
                },
                "providers": {
                    "href": "/api/providers",
                    "revision": revisions["providers"],
                    "cacheable": True,
                },
                "provider_accounts": {
                    "href": "/api/provider-accounts",
                    "revision": revisions["provider_accounts"],
                    "cacheable": False,
                    "load": "explicit_section_only",
                },
                "limits": {
                    "harnesses": MAX_SETTINGS_HARNESSES,
                    "mcp_servers": MAX_SETTINGS_MCP_SERVERS,
                    "mcp_errors": MAX_SETTINGS_MCP_ERRORS,
                    "mcp_history_bytes": MAX_SETTINGS_MCP_HISTORY_BYTES,
                },
            }

        return self._cached("summary", revision, build)

    def runtime(self) -> dict[str, Any]:
        """Return immutable process configuration without a health probe."""
        revision = self._runtime_revision()
        return self._cached(
            "runtime",
            revision,
            lambda: {
                "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
                "revision": revision,
                "runtime": _runtime_projection(self._config),
            },
        )

    def defaults(self) -> dict[str, Any]:
        """Return defaults and static harness metadata without executable probes."""
        snapshot = self._settings_store.load()
        harnesses = tuple(self._registry.list())[:MAX_SETTINGS_HARNESSES]
        revision = self._defaults_revision(snapshot, harnesses=harnesses)
        return self._cached(
            "defaults",
            revision,
            lambda: {
                "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
                "revision": revision,
                "settings_revision": snapshot.revision,
                **_defaults_projection(snapshot, harnesses),
            },
        )

    def workspace(self, workspace: str | None) -> dict[str, Any]:
        """Resolve one selected workspace only when its section is requested."""
        revision = self._workspace_revision(workspace)

        def build() -> dict[str, Any]:
            project = resolve_project(workspace, data_dir=self._config.data_dir)
            state = load_project_state(project)
            return {
                "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
                "revision": revision,
                "workspace": {
                    "project_id": project.id,
                    "name": project.name,
                    "is_git_repo": project.is_git_repo,
                    "trusted": state.trusted,
                    "workspace_policies": ["auto", "current", "worktree"],
                    "permission_profiles": [
                        "interactive",
                        "review_every_action",
                        "unattended",
                    ],
                    "source": "project_state",
                },
            }

        return self._cached("workspace", revision, build)

    def mcp(self, workspace: str | None) -> dict[str, Any]:
        """Return one bounded MCP inventory and a bounded history tail."""
        revision = self._mcp_revision(workspace)

        def build() -> dict[str, Any]:
            project = resolve_project(workspace, data_dir=self._config.data_dir)
            config = load_project_config(project.root)
            descriptors, errors = build_mcp_inventory(
                config.tool_profiles,
                project=project,
            )
            bounded = tuple(descriptors)[:MAX_SETTINGS_MCP_SERVERS]
            history = MCPProbeHistoryStore(self._config.data_dir)
            health = _bounded_mcp_health(
                history.path,
                {descriptor.id for descriptor in bounded},
            )
            return {
                "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
                "revision": revision,
                "mcp": {
                    "servers": [
                        {
                            "id": descriptor.id,
                            "title": descriptor.title,
                            "transport": descriptor.transport.value,
                            "enabled": descriptor.enabled,
                            "trusted": descriptor.trusted,
                            "source": descriptor.source,
                            "health": health.get(descriptor.id, "not_checked"),
                        }
                        for descriptor in bounded
                    ],
                    "errors": list(errors)[:MAX_SETTINGS_MCP_ERRORS],
                    "change_effect": "managed_home_restart",
                    "truncated": {
                        "servers": len(descriptors) > MAX_SETTINGS_MCP_SERVERS,
                        "errors": len(errors) > MAX_SETTINGS_MCP_ERRORS,
                    },
                },
            }

        return self._cached("mcp", revision, build)

    def diagnostics(self) -> dict[str, Any]:
        """Return the current bounded in-memory diagnostic projection."""
        snapshot = self._async_diagnostics.snapshot()
        revision = _digest(snapshot)
        return {
            "schema_version": SETTINGS_SECTION_SCHEMA_VERSION,
            "revision": revision,
            "diagnostics": {
                "content_free": True,
                "actions": [
                    {"id": "check_runtime", "method": "GET", "path": "/api/health"},
                    {
                        "id": "provider_settings",
                        "method": "GET",
                        "path": "/api/providers",
                    },
                ],
                "async_data_plane": snapshot,
            },
        }

    def legacy(self, workspace: str | None) -> dict[str, Any]:
        """Assemble the compatibility response from independently owned sections."""
        runtime = self.runtime()
        defaults = self.defaults()
        workspace_section = self.workspace(workspace)
        mcp = self.mcp(workspace)
        diagnostics = self.diagnostics()
        providers = self._provider_settings_service.list()["providers"]
        return {
            "revision": defaults["settings_revision"],
            "runtime": runtime["runtime"],
            "provider": _provider_summary(providers),
            "routes": defaults["routes"],
            "harness_defaults": defaults["harness_defaults"],
            "workspace": workspace_section["workspace"],
            "mcp": {
                key: value for key, value in mcp["mcp"].items() if key != "truncated"
            },
            "diagnostics": diagnostics["diagnostics"],
        }

    def cache_size(self) -> int:
        """Return the bounded entry count for focused diagnostics and tests."""
        with self._lock:
            return len(self._cache)

    def _runtime_revision(self) -> str:
        return _runtime_revision(self._config)

    def _defaults_revision(
        self,
        snapshot: HarnessDefaultsSnapshot,
        *,
        harnesses: tuple[Any, ...] | None = None,
    ) -> str:
        return _defaults_revision(
            snapshot,
            harnesses or tuple(self._registry.list())[:MAX_SETTINGS_HARNESSES],
        )

    def _workspace_revision(self, workspace: str | None) -> str:
        return _workspace_revision(self._config.data_dir, workspace)

    def _mcp_revision(self, workspace: str | None) -> str:
        return _mcp_revision(self._config.data_dir, workspace)

    def _cached(
        self,
        section: str,
        revision: str,
        build: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        key = (section, revision)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
        payload = build()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
            return deepcopy(payload)
