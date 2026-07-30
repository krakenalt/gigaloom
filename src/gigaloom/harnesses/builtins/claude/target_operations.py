"""Claude target lifecycle operations."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
from typing import Any

from gigaloom.integration_installer import (
    InstallationConflictError,
    InstallationScopeError,
    InstallationStateError,
)
from gigaloom.integration_packages import (
    InstallationScope,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_semantic_hash,
)
from gigaloom.harnesses.ports import exclusive_file_lock


from gigaloom.harnesses.builtins.claude.target_contracts import (
    CLAUDE_PLUGIN_COMMAND_TIMEOUT_SECONDS,
    ClaudePluginApproval,
    ClaudePluginCommandError,
    ClaudePluginCommandResult,
    ClaudePluginHealth,
    ClaudePluginPlan,
    ClaudePluginPolicyError,
    ClaudePluginRequest,
    ClaudePluginResult,
    ClaudePluginSource,
    ClaudePluginSourceKind,
    _ClaudeExecutionContext,
    _absolute_path,
)
from gigaloom.harnesses.builtins.claude.target_source import (
    _inspect_local_source,
    _isolated_env,
    _json_hash,
    _marketplace_source_arg,
    _marketplace_source_matches,
    _required_string,
)


class _ClaudeTargetOperations:
    def _set_enabled(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
        *,
        enabled: bool,
    ) -> ClaudePluginResult:
        action = "enable" if enabled else "disable"
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            self._authorize(request, plan, approval, action=action)
            self._require_inactive(root, action)
            self._command(
                context,
                (
                    "plugin",
                    action,
                    self._selector(request),
                    "--scope",
                    context.native_scope,
                ),
            )
            health = self.verify(request)
            if health.status != "healthy" or health.enabled is not enabled:
                raise ClaudePluginCommandError(
                    f"Claude plugin {action} did not produce exact native discovery"
                )
        return self._result(action, f"{action}d", request, health)

    def _preview(
        self, request: ClaudePluginRequest, *, action: str
    ) -> ClaudePluginPlan:
        root, context = self._admit(request)
        source_hash = self._validate_source(request, context)
        current = self._current_plugin(context, self._selector(request))
        current_version = (
            _required_string(current, "version", "Claude plugin version")
            if current is not None
            else None
        )
        current_enabled = (
            current.get("enabled") is True if current is not None else None
        )
        if action == "install" and current is not None:
            raise InstallationConflictError(
                "Claude plugin is already installed; use update or uninstall"
            )
        if action in {"enable", "disable", "update", "uninstall"} and current is None:
            raise InstallationConflictError(
                f"Claude plugin {action} requires a native installation"
            )
        if action == "enable" and current_enabled:
            raise InstallationConflictError("Claude plugin is already enabled")
        if action == "disable" and not current_enabled:
            raise InstallationConflictError("Claude plugin is already disabled")
        policy_status = self._policy_status(action, request)
        if (
            policy_status == "allowed"
            and action == "update"
            and request.source.kind is ClaudePluginSourceKind.GIT
        ):
            policy_status = "provider_handoff_required"
        command_ids = {
            "install": (
                "plugin-validate",
                "marketplace-add",
                "plugin-install",
                "plugin-list",
            ),
            "enable": ("plugin-enable", "plugin-list"),
            "disable": ("plugin-disable", "plugin-list"),
            "update": (
                ("native-handoff",)
                if request.source.kind is ClaudePluginSourceKind.GIT
                else ("marketplace-update", "plugin-update", "plugin-list")
            ),
            "uninstall": ("plugin-uninstall", "plugin-list"),
        }[action]
        network_required = (
            request.source.kind is ClaudePluginSourceKind.GIT
            and action in {"install", "update"}
        )
        semantic = {
            "action": action,
            "package_id": request.package.id,
            "package_version": request.package.version,
            "manifest_sha256": integration_package_semantic_hash(request.package),
            "plugin_id": self._selector(request),
            "source_sha256": source_hash,
            "scope": request.scope.value,
            "root": str(root),
            "native_scope": context.native_scope,
            "expected_version": current_version,
            "expected_enabled": current_enabled,
            "network_required": network_required,
            "native_consent_required": True,
            "restart_required": True,
            "policy_status": policy_status,
            "command_ids": list(command_ids),
        }
        return ClaudePluginPlan(
            action=action,
            plan_id=f"plan_{_json_hash(semantic)}",
            package_id=request.package.id,
            package_version=request.package.version,
            manifest_sha256=semantic["manifest_sha256"],
            plugin_id=self._selector(request),
            source_sha256=source_hash,
            scope=request.scope,
            root=root,
            native_scope=context.native_scope,
            expected_version=current_version,
            expected_enabled=current_enabled,
            network_required=network_required,
            native_consent_required=True,
            restart_required=True,
            policy_status=policy_status,
            command_ids=command_ids,
        )

    def _authorize(
        self,
        request: ClaudePluginRequest,
        plan: ClaudePluginPlan,
        approval: ClaudePluginApproval,
        *,
        action: str,
    ) -> None:
        current = self._preview(request, action=action)
        if current != plan:
            raise InstallationConflictError(
                "Claude plugin source or native state changed after preview"
            )
        if approval.plan_id != plan.plan_id:
            raise InstallationConflictError(
                "Claude plugin approval does not match the preview"
            )
        if plan.policy_status != "allowed":
            raise ClaudePluginPolicyError(
                f"Claude plugin action denied: {plan.policy_status}"
            )
        if not approval.native_consent_acknowledged:
            raise ClaudePluginPolicyError(
                "Claude plugin action requires explicit native consent acknowledgement"
            )
        if plan.network_required and not approval.allow_network:
            raise ClaudePluginPolicyError(
                "Claude Git marketplace action requires explicit network approval"
            )
        if (
            request.scope is InstallationScope.USER_HOME
            and not approval.allow_user_home
        ):
            raise InstallationScopeError(
                "Claude user-home plugin action requires explicit approval"
            )

    def _policy_status(self, action: str, request: ClaudePluginRequest) -> str:
        assessment = assess_integration_package(request.package)
        if assessment.decision is IntegrationTrustDecision.BLOCKED:
            return "package_blocked"
        if not self.policy(action, request.package, request.scope):
            return "managed_policy_denied"
        return "allowed"

    def _validate_source(
        self,
        request: ClaudePluginRequest,
        context: _ClaudeExecutionContext,
    ) -> str:
        if request.source.kind is ClaudePluginSourceKind.GIT:
            return _json_hash(
                {
                    "marketplace_name": request.source.marketplace_name,
                    "location": request.source.location,
                    "ref": request.source.ref,
                    "sparse": list(request.source.sparse),
                    "package_checksum": request.package.checksum,
                }
            )
        root = _absolute_path(Path(request.source.location))
        if root not in self.source_roots:
            raise InstallationScopeError(
                "Claude local marketplace source is not explicitly admitted"
            )
        inspection = _inspect_local_source(root, request.source, request.plugin_name)
        if inspection["version"] != request.package.version:
            raise InstallationConflictError(
                "Claude plugin manifest version does not match the package"
            )
        if inspection["checksum"] != request.package.checksum:
            raise InstallationConflictError(
                "Claude plugin source checksum does not match the package"
            )
        result = self._run(context, ("plugin", "validate", "--strict", str(root)))
        if result.returncode != 0:
            raise ClaudePluginCommandError(
                "Claude strict plugin validation rejected the reviewed source"
            )
        return str(inspection["source_sha256"])

    def _admit(
        self, request: ClaudePluginRequest
    ) -> tuple[Path, _ClaudeExecutionContext]:
        root = _absolute_path(request.root)
        context = self._context(root, request.scope)
        return root, context

    def _context(self, root: Path, scope: InstallationScope) -> _ClaudeExecutionContext:
        root = _absolute_path(root)
        if scope is InstallationScope.MANAGED_HOME:
            if root not in self.managed_roots:
                raise InstallationScopeError(
                    "Claude managed plugin root is not explicitly admitted"
                )
            context = _ClaudeExecutionContext(root, None, "user")
        elif scope is InstallationScope.PROJECT:
            if root not in self.project_roots:
                raise InstallationScopeError(
                    "Claude project plugin root is not explicitly admitted"
                )
            identity = _json_hash({"project_root": str(root)})
            config_dir = (
                self.data_dir / "native" / "claude" / "plugin-projects" / identity
            )
            context = _ClaudeExecutionContext(config_dir, root, "project")
        elif (
            not self.allow_user_home
            or self.user_home_root is None
            or root != self.user_home_root
        ):
            raise InstallationScopeError(
                "Claude user-home plugin root is disabled or mismatched"
            )
        else:
            context = _ClaudeExecutionContext(root, None, "user")
        if not root.is_dir() or root.is_symlink():
            raise InstallationScopeError(
                "Claude plugin root must be an existing regular directory"
            )
        return context

    def _ensure_marketplace(
        self,
        context: _ClaudeExecutionContext,
        source: ClaudePluginSource,
    ) -> bool:
        matches = self._matching_marketplaces(context, source.marketplace_name)
        if matches:
            if not _marketplace_source_matches(matches[0], source):
                raise InstallationConflictError(
                    "Claude marketplace name is registered to another source"
                )
            return False
        self._command(context, self._marketplace_add_args(source, context.native_scope))
        matches = self._matching_marketplaces(context, source.marketplace_name)
        if len(matches) != 1 or not _marketplace_source_matches(matches[0], source):
            raise ClaudePluginCommandError(
                "Claude marketplace registration did not preserve the reviewed source"
            )
        return True

    @staticmethod
    def _marketplace_add_args(
        source: ClaudePluginSource, native_scope: str
    ) -> tuple[str, ...]:
        args: list[str] = [
            "plugin",
            "marketplace",
            "add",
            _marketplace_source_arg(source),
            "--scope",
            native_scope,
        ]
        if source.sparse:
            args.append("--sparse")
            args.extend(source.sparse)
        return tuple(args)

    def _registered_source_matches(
        self,
        context: _ClaudeExecutionContext,
        source: ClaudePluginSource,
    ) -> bool:
        matches = self._matching_marketplaces(context, source.marketplace_name)
        return len(matches) == 1 and _marketplace_source_matches(matches[0], source)

    def _matching_marketplaces(
        self, context: _ClaudeExecutionContext, name: str
    ) -> tuple[Mapping[str, Any], ...]:
        matches = tuple(
            item for item in self._marketplace_list(context) if item.get("name") == name
        )
        if len(matches) > 1:
            raise InstallationStateError("Claude marketplace list has duplicate names")
        return matches

    def _marketplace_list(
        self, context: _ClaudeExecutionContext
    ) -> tuple[Mapping[str, Any], ...]:
        payload = self._json_array_command(
            context, ("plugin", "marketplace", "list", "--json")
        )
        return tuple(payload)

    def _plugin_list(
        self, context: _ClaudeExecutionContext
    ) -> tuple[Mapping[str, Any], ...]:
        return tuple(self._json_array_command(context, ("plugin", "list", "--json")))

    def _current_plugin(
        self, context: _ClaudeExecutionContext, selector: str
    ) -> Mapping[str, Any] | None:
        matches = [
            item
            for item in self._plugin_list(context)
            if item.get("id") == selector and item.get("scope") == context.native_scope
        ]
        if len(matches) > 1:
            raise InstallationStateError("Claude plugin list has duplicate identities")
        return matches[0] if matches else None

    def _json_array_command(
        self, context: _ClaudeExecutionContext, args: tuple[str, ...]
    ) -> list[Mapping[str, Any]]:
        result = self._command(context, args)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudePluginCommandError(
                "Claude command returned invalid JSON"
            ) from exc
        if not isinstance(payload, list) or any(
            not isinstance(item, Mapping) for item in payload
        ):
            raise ClaudePluginCommandError("Claude command JSON must be an object list")
        return payload

    def _command(
        self, context: _ClaudeExecutionContext, args: tuple[str, ...]
    ) -> ClaudePluginCommandResult:
        result = self._run(context, args)
        if result.returncode != 0:
            raise ClaudePluginCommandError(
                f"Claude command {args[0]} failed with status {result.returncode}"
            )
        return result

    def _run(
        self, context: _ClaudeExecutionContext, args: tuple[str, ...]
    ) -> ClaudePluginCommandResult:
        return self.command_runner(
            self.executable + args,
            _isolated_env(context.config_dir),
            context.cwd,
            CLAUDE_PLUGIN_COMMAND_TIMEOUT_SECONDS,
        )

    def _best_effort_cleanup(
        self,
        context: _ClaudeExecutionContext,
        request: ClaudePluginRequest,
    ) -> None:
        try:
            if self._current_plugin(context, self._selector(request)) is not None:
                self._run(
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
            self._run(
                context,
                (
                    "plugin",
                    "marketplace",
                    "remove",
                    request.source.marketplace_name,
                    "--scope",
                    context.native_scope,
                ),
            )
        except Exception:
            # Best-effort rollback must not mask the original installation failure.
            pass

    def _lock_path(self, root: Path, scope: InstallationScope) -> Path:
        if self.locks_root.is_symlink():
            raise InstallationStateError("Claude plugin lock root cannot be a symlink")
        self.locks_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.locks_root.is_symlink():
            raise InstallationStateError("Claude plugin lock root cannot be a symlink")
        os.chmod(self.locks_root, 0o700)
        return self.locks_root / _json_hash({"root": str(root), "scope": scope.value})

    def _require_inactive(self, root: Path, action: str) -> None:
        if self.target_active(root):
            raise InstallationConflictError(
                f"Claude plugin target is active; stop it before {action}"
            )

    @staticmethod
    def _selector(request: ClaudePluginRequest) -> str:
        return f"{request.plugin_name}@{request.source.marketplace_name}"

    @staticmethod
    def _result(
        action: str,
        status: str,
        request: ClaudePluginRequest,
        health: ClaudePluginHealth,
    ) -> ClaudePluginResult:
        return ClaudePluginResult(
            action=action,
            status=status,
            plugin_id=health.plugin_id,
            package_id=request.package.id,
            version=health.version,
            scope=request.scope,
            enabled=health.enabled,
            restart_required=True,
        )
