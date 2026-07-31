"""Claude provider-specific target driver."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import tempfile

from gigaloom.integration_installer import (
    InstallationConflictError,
)
from gigaloom.integration_packages import (
    InstallationScope,
)
from gigaloom.harnesses.ports import exclusive_file_lock
from gigaloom.types import redact_secrets


from gigaloom.harnesses.builtins.claude.target_contracts import (
    CLAUDE_PLUGIN_TARGET_DESCRIPTOR,
    ClaudePluginApproval,
    ClaudePluginCommandError,
    ClaudePluginCommandRunner,
    ClaudePluginHandoff,
    ClaudePluginHealth,
    ClaudePluginInstallation,
    ClaudePluginPlan,
    ClaudePluginPolicy,
    ClaudePluginProbe,
    ClaudePluginRequest,
    ClaudePluginResult,
    _ClaudeExecutionContext,
    _absolute_path,
)
from gigaloom.harnesses.builtins.claude.target_source import (
    _bounded_output,
    _first_line,
    _installation_from_item,
    _is_relative_to,
    _marketplace_source_arg,
    _normalize_roots,
    _required_string,
    _run_command,
    _supported_claude_version,
)
from gigaloom.harnesses.builtins.claude.target_operations import (
    _ClaudeTargetOperations,
)


class ClaudePluginTargetDriver(_ClaudeTargetOperations):
    """Operate Claude plugins only through documented native CLI commands."""

    descriptor = CLAUDE_PLUGIN_TARGET_DESCRIPTOR

    def __init__(
        self,
        data_dir: str | Path,
        *,
        managed_roots: Sequence[str | Path] = (),
        project_roots: Sequence[str | Path] = (),
        source_roots: Sequence[str | Path] = (),
        user_home_root: str | Path | None = None,
        allow_user_home: bool = False,
        executable: Sequence[str] = ("claude",),
        command_runner: ClaudePluginCommandRunner | None = None,
        policy: ClaudePluginPolicy | None = None,
        target_active: Callable[[Path], bool] | None = None,
    ) -> None:
        self.data_dir = _absolute_path(Path(data_dir))
        self.locks_root = self.data_dir / "integrations" / "claude-plugin" / "locks"
        native_root = self.data_dir / "native"
        self.managed_roots = _normalize_roots(managed_roots)
        for root in self.managed_roots:
            if not _is_relative_to(root, native_root):
                raise ValueError("managed Claude plugin roots must be Harness-native")
        self.project_roots = _normalize_roots(project_roots)
        self.source_roots = _normalize_roots(source_roots)
        self.user_home_root = (
            _absolute_path(Path(user_home_root)) if user_home_root is not None else None
        )
        self.allow_user_home = allow_user_home
        self.executable = tuple(str(item) for item in executable)
        if not self.executable or any(not item for item in self.executable):
            raise ValueError("Claude plugin executable is invalid")
        self.command_runner = command_runner or _run_command
        self.policy = policy or (lambda _action, _package, _scope: True)
        if not callable(self.policy):
            raise TypeError("Claude plugin policy must be callable")
        self.target_active = target_active or (lambda _root: False)

    def probe_target(self) -> ClaudePluginProbe:
        """Probe documented plugin surfaces in an isolated temporary home."""
        with tempfile.TemporaryDirectory(prefix="gigaloom-claude-plugin-probe-") as raw:
            context = _ClaudeExecutionContext(Path(raw) / ".claude", None, "user")
            commands = (
                ("--version",),
                ("plugin", "--help"),
                ("plugin", "validate", "--help"),
                ("plugin", "install", "--help"),
                ("plugin", "list", "--help"),
                ("plugin", "update", "--help"),
                ("plugin", "uninstall", "--help"),
                ("plugin", "enable", "--help"),
                ("plugin", "disable", "--help"),
                ("plugin", "marketplace", "add", "--help"),
                ("plugin", "marketplace", "list", "--help"),
                ("plugin", "marketplace", "update", "--help"),
                ("plugin", "marketplace", "remove", "--help"),
            )
            results = tuple(self._run(context, args) for args in commands)
        version = _first_line(results[0].stdout or results[0].stderr)
        texts = tuple(_bounded_output(item) for item in results[1:])
        expectations = (
            ("plugin_validate_strict", "--strict", texts[1]),
            ("plugin_install_scope", "--scope", texts[2]),
            ("plugin_list_json", "--json", texts[3]),
            ("plugin_update_scope", "--scope", texts[4]),
            ("plugin_uninstall_scope", "--scope", texts[5]),
            ("plugin_enable_scope", "--scope", texts[6]),
            ("plugin_disable_scope", "--scope", texts[7]),
            ("marketplace_add_scope", "--scope", texts[8]),
            ("marketplace_list_json", "--json", texts[9]),
            ("marketplace_update", "update", texts[10]),
            ("marketplace_remove_scope", "--scope", texts[11]),
        )
        capabilities = tuple(
            name for name, needle, text in expectations if needle in text
        )
        supported = (
            all(item.returncode == 0 for item in results)
            and len(capabilities) == len(expectations)
            and _supported_claude_version(version)
        )
        return ClaudePluginProbe(
            status="supported" if supported else "unsupported",
            version=version,
            command=str(redact_secrets(self.executable[0])),
            capabilities=capabilities,
            evidence="bounded --version and documented plugin help probes",
        )

    def discover_installed(self) -> tuple[ClaudePluginInstallation, ...]:
        """Discover plugins through native JSON for configured roots only."""
        roots = [(root, InstallationScope.MANAGED_HOME) for root in self.managed_roots]
        roots.extend((root, InstallationScope.PROJECT) for root in self.project_roots)
        if self.allow_user_home and self.user_home_root is not None:
            roots.append((self.user_home_root, InstallationScope.USER_HOME))
        discovered: list[ClaudePluginInstallation] = []
        for root, scope in roots:
            context = self._context(root, scope)
            for item in self._plugin_list(context):
                if item.get("scope") != context.native_scope:
                    continue
                discovered.append(_installation_from_item(item, root, scope))
        return tuple(
            sorted(discovered, key=lambda item: (str(item.root), item.plugin_id))
        )

    def preview_install(self, request: ClaudePluginRequest) -> ClaudePluginPlan:
        """Preview exact source registration and native installation commands."""
        return self._preview(request, action="install")

    def install(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
    ) -> ClaudePluginResult:
        """Register a source and install through the documented native CLI."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            self._authorize(request, plan, approval, action="install")
            self._require_inactive(root, "install")
            added_marketplace = self._ensure_marketplace(context, request.source)
            try:
                self._command(
                    context,
                    (
                        "plugin",
                        "install",
                        self._selector(request),
                        "--scope",
                        context.native_scope,
                    ),
                )
                health = self.verify(request)
                if health.status != "healthy":
                    raise ClaudePluginCommandError(
                        "Claude plugin install did not produce exact native discovery"
                    )
            except Exception:
                if added_marketplace:
                    self._best_effort_cleanup(context, request)
                raise
        return self._result("install", "installed", request, health)

    def verify(self, request: ClaudePluginRequest) -> ClaudePluginHealth:
        """Verify exact identity through native JSON without reading plugin caches."""
        _root, context = self._admit(request)
        current = self._current_plugin(context, self._selector(request))
        if current is None:
            return ClaudePluginHealth(
                plugin_id=self._selector(request),
                package_id=request.package.id,
                version="unknown",
                enabled=False,
                exact_version=False,
                exact_source=False,
                status="missing",
            )
        version = _required_string(current, "version", "Claude plugin version")
        exact_source = self._registered_source_matches(context, request.source)
        exact_version = version == request.package.version
        enabled = current.get("enabled") is True
        return ClaudePluginHealth(
            plugin_id=self._selector(request),
            package_id=request.package.id,
            version=version,
            enabled=enabled,
            exact_version=exact_version,
            exact_source=exact_source,
            status="healthy" if exact_version and exact_source else "degraded",
        )

    def health(self, request: ClaudePluginRequest) -> ClaudePluginHealth:
        """Alias native exact-discovery verification."""
        return self.verify(request)

    def preview_enable(self, request: ClaudePluginRequest) -> ClaudePluginPlan:
        """Preview native plugin enablement."""
        return self._preview(request, action="enable")

    def enable(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
    ) -> ClaudePluginResult:
        """Enable through the documented native CLI after exact approval."""
        return self._set_enabled(request, plan, approval, enabled=True)

    def preview_disable(self, request: ClaudePluginRequest) -> ClaudePluginPlan:
        """Preview native plugin disablement."""
        return self._preview(request, action="disable")

    def disable(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
    ) -> ClaudePluginResult:
        """Disable through the documented native CLI after exact approval."""
        return self._set_enabled(request, plan, approval, enabled=False)

    def preview_update(self, request: ClaudePluginRequest) -> ClaudePluginPlan:
        """Preview a reviewed marketplace refresh and native update."""
        return self._preview(request, action="update")

    def update(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
    ) -> ClaudePluginResult | ClaudePluginHandoff:
        """Update local sources; hand immutable Git ref replacement to Claude."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            if plan.policy_status == "provider_handoff_required":
                current = self._preview(request, action="update")
                if current != plan or approval.plan_id != plan.plan_id:
                    raise InstallationConflictError(
                        "Claude plugin source or native state changed after preview"
                    )
                return ClaudePluginHandoff(
                    action="update",
                    plugin_id=self._selector(request),
                    command=self.executable
                    + (
                        "plugin",
                        "marketplace",
                        "add",
                        _marketplace_source_arg(request.source),
                        "--scope",
                        context.native_scope,
                    ),
                    interaction=(
                        "replace the reviewed immutable marketplace ref, then run "
                        "the native plugin update command"
                    ),
                    consent_owner="claude",
                    restart_required=True,
                    reason=(
                        "Claude exposes marketplace replacement and plugin update as "
                        "separate non-atomic commands"
                    ),
                )
            self._authorize(request, plan, approval, action="update")
            self._require_inactive(root, "update")
            self._command(
                context,
                (
                    "plugin",
                    "marketplace",
                    "update",
                    request.source.marketplace_name,
                ),
            )
            self._command(
                context,
                (
                    "plugin",
                    "update",
                    self._selector(request),
                    "--scope",
                    context.native_scope,
                ),
            )
            health = self.verify(request)
            if health.status != "healthy":
                raise ClaudePluginCommandError(
                    "Claude plugin update did not produce exact native discovery"
                )
        return self._result("update", "updated", request, health)

    def preview_uninstall(self, request: ClaudePluginRequest) -> ClaudePluginPlan:
        """Preview exact native removal without provider-home cache writes."""
        return self._preview(request, action="uninstall")

    def uninstall(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
    ) -> ClaudePluginResult:
        """Remove one plugin through the documented native CLI."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            self._authorize(request, plan, approval, action="uninstall")
            self._require_inactive(root, "uninstall")
            self._command(
                context,
                (
                    "plugin",
                    "uninstall",
                    self._selector(request),
                    "--scope",
                    context.native_scope,
                    "--yes",
                ),
            )
            if self._current_plugin(context, self._selector(request)) is not None:
                raise ClaudePluginCommandError(
                    "Claude plugin uninstall did not remove native discovery"
                )
        return ClaudePluginResult(
            action="uninstall",
            status="uninstalled",
            plugin_id=self._selector(request),
            package_id=request.package.id,
            version=None,
            scope=request.scope,
            enabled=False,
            restart_required=True,
        )

    def rollback(self, request: ClaudePluginRequest) -> ClaudePluginHandoff:
        """Expose truthful reviewed-source rollback when no atomic CLI exists."""
        _root, context = self._admit(request)
        if self.verify(request).status == "missing":
            raise InstallationConflictError(
                "Claude plugin rollback requires a native installation"
            )
        return ClaudePluginHandoff(
            action="rollback",
            plugin_id=self._selector(request),
            command=self.executable
            + (
                "plugin",
                "update",
                self._selector(request),
                "--scope",
                context.native_scope,
            ),
            interaction="restore the previously reviewed marketplace version, then update",
            consent_owner="claude",
            restart_required=True,
            reason="Claude exposes update but no atomic version-selecting rollback command",
        )
