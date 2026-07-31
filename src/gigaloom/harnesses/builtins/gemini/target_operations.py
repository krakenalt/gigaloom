"""Gemini target lifecycle operations."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from gigaloom.integration_installer import (
    InstallationConflictError,
    InstallationScopeError,
)
from gigaloom.integration_packages import (
    InstallationScope,
    IntegrationTrustDecision,
    assess_integration_package,
    integration_package_semantic_hash,
)
from gigaloom.harnesses.ports import exclusive_file_lock


from gigaloom.harnesses.builtins.gemini.target_contracts import (
    GEMINI_EXTENSION_COMMAND_TIMEOUT_SECONDS,
    GeminiExtensionApproval,
    GeminiExtensionCommandError,
    GeminiExtensionCommandResult,
    GeminiExtensionHandoff,
    GeminiExtensionHealth,
    GeminiExtensionPlan,
    GeminiExtensionPolicyError,
    GeminiExtensionRequest,
    GeminiExtensionResult,
    GeminiExtensionSourceKind,
    _GeminiExecutionContext,
    _absolute_path,
)
from gigaloom.harnesses.builtins.gemini.target_source import (
    _inspect_local_source,
    _isolated_env,
    _json_hash,
    _required_string,
    _source_metadata_hash,
)


class _GeminiTargetOperations:
    def _gallery_handoff(
        self, request: GeminiExtensionRequest
    ) -> GeminiExtensionHandoff:
        return GeminiExtensionHandoff(
            action="select_source",
            extension_name=request.extension_name,
            command=(),
            interaction=(
                "open the Gemini extension gallery entry, select and review its "
                "repository, then create an immutable Git installation request"
            ),
            consent_owner="gemini_cli",
            restart_required=False,
            reason=(
                "the documented Gemini gallery is discovery-only and exposes no "
                "marketplace registration API"
            ),
        )

    def _set_enabled(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
        *,
        enabled: bool,
    ) -> GeminiExtensionResult:
        action = "enable" if enabled else "disable"
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            self._authorize(request, plan, approval, action=action)
            self._require_inactive(root, action)
            self._command(
                context,
                (
                    "extensions",
                    action,
                    request.extension_name,
                    "--scope",
                    context.native_scope,
                ),
            )
            health = self.verify(request)
            if health.status != "healthy" or health.enabled is not enabled:
                raise GeminiExtensionCommandError(
                    f"Gemini extension {action} did not produce exact native discovery"
                )
        return self._result(action, f"{action}d", request, health)

    def _preview(
        self, request: GeminiExtensionRequest, *, action: str
    ) -> GeminiExtensionPlan:
        root, context = self._admit(request)
        source_hash = self._validate_source(request, context)
        current = self._current(context, request.extension_name)
        current_version = (
            _required_string(current, "version", "Gemini extension version")
            if current is not None
            else None
        )
        current_enabled = (
            current.get("isActive") is True if current is not None else None
        )
        current_source_hash = (
            _source_metadata_hash(current) if current is not None else None
        )
        if action == "install" and current is not None:
            raise InstallationConflictError(
                "Gemini extension is already installed; use update or uninstall"
            )
        if action in {"enable", "disable", "update", "uninstall"} and current is None:
            raise InstallationConflictError(
                f"Gemini extension {action} requires a native installation"
            )
        if action == "enable" and current_enabled:
            raise InstallationConflictError("Gemini extension is already enabled")
        if action == "disable" and not current_enabled:
            raise InstallationConflictError("Gemini extension is already disabled")
        policy_status = self._policy_status(action, request)
        if policy_status == "allowed" and (
            request.source.kind is GeminiExtensionSourceKind.GALLERY
            or (
                action == "update"
                and request.source.kind is GeminiExtensionSourceKind.GIT
            )
        ):
            policy_status = "provider_handoff_required"
        command_ids = {
            "install": (
                ("gallery-handoff",)
                if request.source.kind is GeminiExtensionSourceKind.GALLERY
                else ("extension-validate", "extension-install", "extension-list")
            ),
            "enable": ("extension-enable", "extension-list"),
            "disable": ("extension-disable", "extension-list"),
            "update": (
                ("native-handoff",)
                if request.source.kind
                in {
                    GeminiExtensionSourceKind.GIT,
                    GeminiExtensionSourceKind.GALLERY,
                }
                else ("extension-update", "extension-list")
            ),
            "uninstall": ("extension-uninstall", "extension-list"),
        }[action]
        network_required = (
            request.source.kind is GeminiExtensionSourceKind.GIT
            and action
            in {
                "install",
                "update",
            }
        )
        source_trust_required = action == "install" and request.source.kind in {
            GeminiExtensionSourceKind.LOCAL,
            GeminiExtensionSourceKind.GIT,
        }
        semantic = {
            "action": action,
            "package_id": request.package.id,
            "package_version": request.package.version,
            "manifest_sha256": integration_package_semantic_hash(request.package),
            "extension_name": request.extension_name,
            "source_sha256": source_hash,
            "scope": request.scope.value,
            "root": str(root),
            "native_scope": context.native_scope,
            "expected_version": current_version,
            "expected_enabled": current_enabled,
            "expected_source_sha256": current_source_hash,
            "network_required": network_required,
            "native_consent_required": action in {"install", "update"},
            "source_trust_required": source_trust_required,
            "restart_required": action != "install"
            or request.source.kind is not GeminiExtensionSourceKind.GALLERY,
            "policy_status": policy_status,
            "command_ids": list(command_ids),
        }
        return GeminiExtensionPlan(
            action=action,
            plan_id=f"plan_{_json_hash(semantic)}",
            package_id=request.package.id,
            package_version=request.package.version,
            manifest_sha256=str(semantic["manifest_sha256"]),
            extension_name=request.extension_name,
            source_sha256=source_hash,
            scope=request.scope,
            root=root,
            native_scope=context.native_scope,
            expected_version=current_version,
            expected_enabled=current_enabled,
            expected_source_sha256=current_source_hash,
            network_required=network_required,
            native_consent_required=bool(semantic["native_consent_required"]),
            source_trust_required=source_trust_required,
            restart_required=bool(semantic["restart_required"]),
            policy_status=policy_status,
            command_ids=command_ids,
        )

    def _authorize(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
        *,
        action: str,
    ) -> None:
        current = self._preview(request, action=action)
        if current != plan:
            raise InstallationConflictError(
                "Gemini extension source or native state changed after preview"
            )
        if approval.plan_id != plan.plan_id:
            raise InstallationConflictError(
                "Gemini extension approval does not match the preview"
            )
        if plan.policy_status != "allowed":
            raise GeminiExtensionPolicyError(
                f"Gemini extension action denied: {plan.policy_status}"
            )
        if plan.native_consent_required and not approval.native_consent_acknowledged:
            raise GeminiExtensionPolicyError(
                "Gemini extension action requires explicit native consent acknowledgement"
            )
        if plan.source_trust_required and not approval.source_trust_acknowledged:
            raise GeminiExtensionPolicyError(
                "Gemini extension install requires explicit source trust acknowledgement"
            )
        if plan.network_required and not approval.allow_network:
            raise GeminiExtensionPolicyError(
                "Gemini Git extension action requires explicit network approval"
            )
        if (
            request.scope is InstallationScope.USER_HOME
            and not approval.allow_user_home
        ):
            raise InstallationScopeError(
                "Gemini user-home extension action requires explicit approval"
            )

    def _authorize_handoff(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
        *,
        action: str,
    ) -> None:
        current = self._preview(request, action=action)
        if current != plan or approval.plan_id != plan.plan_id:
            raise InstallationConflictError(
                "Gemini extension handoff no longer matches the preview"
            )
        if plan.policy_status != "provider_handoff_required":
            raise GeminiExtensionPolicyError("Gemini extension handoff is not required")

    def _policy_status(self, action: str, request: GeminiExtensionRequest) -> str:
        assessment = assess_integration_package(request.package)
        if assessment.decision is IntegrationTrustDecision.BLOCKED:
            return "package_blocked"
        if not self.policy(action, request.package, request.scope):
            return "managed_policy_denied"
        return "allowed"

    def _validate_source(
        self,
        request: GeminiExtensionRequest,
        context: _GeminiExecutionContext,
    ) -> str:
        if request.source.kind is GeminiExtensionSourceKind.GALLERY:
            return _json_hash(
                {
                    "gallery": request.source.location,
                    "package_checksum": request.package.checksum,
                }
            )
        if request.source.kind is GeminiExtensionSourceKind.GIT:
            return _json_hash(
                {
                    "location": request.source.location,
                    "ref": request.source.ref,
                    "package_checksum": request.package.checksum,
                }
            )
        root = _absolute_path(Path(request.source.location))
        if root not in self.source_roots:
            raise InstallationScopeError(
                "Gemini local extension source is not explicitly admitted"
            )
        inspection = _inspect_local_source(root, request.extension_name)
        if inspection["version"] != request.package.version:
            raise InstallationConflictError(
                "Gemini extension manifest version does not match the package"
            )
        if inspection["checksum"] != request.package.checksum:
            raise InstallationConflictError(
                "Gemini extension source checksum does not match the package"
            )
        result = self._run(context, ("extensions", "validate", str(root)))
        if result.returncode != 0:
            raise GeminiExtensionCommandError(
                "Gemini native validation rejected the reviewed extension"
            )
        return str(inspection["source_sha256"])

    def _admit(
        self, request: GeminiExtensionRequest
    ) -> tuple[Path, _GeminiExecutionContext]:
        root = _absolute_path(request.root)
        return root, self._context(root, request.scope)

    def _context(self, root: Path, scope: InstallationScope) -> _GeminiExecutionContext:
        root = _absolute_path(root)
        if scope is InstallationScope.MANAGED_HOME:
            if root not in self.managed_roots:
                raise InstallationScopeError(
                    "Gemini managed extension root is not explicitly admitted"
                )
            context = _GeminiExecutionContext(root, root, "workspace")
        elif scope is InstallationScope.PROJECT:
            if root not in self.project_roots:
                raise InstallationScopeError(
                    "Gemini project extension root is not explicitly admitted"
                )
            identity = _json_hash({"project_root": str(root)})
            config_home = (
                self.data_dir / "native" / "gemini" / "extension-projects" / identity
            )
            context = _GeminiExecutionContext(config_home, root, "workspace")
        elif (
            not self.allow_user_home
            or self.user_home_root is None
            or root != self.user_home_root
        ):
            raise InstallationScopeError(
                "Gemini user-home extension root is disabled or mismatched"
            )
        else:
            context = _GeminiExecutionContext(root, root, "workspace")
        if not root.is_dir() or root.is_symlink():
            raise InstallationScopeError(
                "Gemini extension root must be an existing regular directory"
            )
        return context

    def _list(self, context: _GeminiExecutionContext) -> tuple[Mapping[str, Any], ...]:
        result = self._command(
            context,
            ("extensions", "list", "--output-format", "json"),
        )
        candidates: list[list[Mapping[str, Any]]] = []
        for channel in (result.stdout, result.stderr):
            if not channel.strip():
                continue
            try:
                value = json.loads(channel)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, list) and all(
                isinstance(item, dict) for item in value
            ):
                candidates.append(value)
        if len(candidates) != 1:
            raise GeminiExtensionCommandError(
                "Gemini extension list returned invalid or ambiguous JSON"
            )
        return tuple(candidates[0])

    def _current(
        self, context: _GeminiExecutionContext, extension_name: str
    ) -> Mapping[str, Any] | None:
        matches = [
            item for item in self._list(context) if item.get("name") == extension_name
        ]
        if len(matches) > 1:
            raise GeminiExtensionCommandError(
                "Gemini extension discovery returned duplicate names"
            )
        return matches[0] if matches else None

    def _command(
        self,
        context: _GeminiExecutionContext,
        args: tuple[str, ...],
        *,
        trust_workspace: bool = False,
    ) -> GeminiExtensionCommandResult:
        result = self._run(context, args, trust_workspace=trust_workspace)
        if result.returncode != 0:
            raise GeminiExtensionCommandError(
                "Gemini extension native command failed with redacted diagnostics"
            )
        return result

    def _run(
        self,
        context: _GeminiExecutionContext,
        args: tuple[str, ...],
        *,
        trust_workspace: bool = False,
    ) -> GeminiExtensionCommandResult:
        return self.command_runner(
            self.executable + args,
            _isolated_env(context.config_home, trust_workspace=trust_workspace),
            context.cwd,
            GEMINI_EXTENSION_COMMAND_TIMEOUT_SECONDS,
        )

    def _best_effort_uninstall(
        self, context: _GeminiExecutionContext, extension_name: str
    ) -> None:
        try:
            if self._current(context, extension_name) is not None:
                self._run(context, ("extensions", "uninstall", extension_name))
        except Exception:
            return

    def _lock_path(self, root: Path, scope: InstallationScope) -> Path:
        identity = _json_hash({"root": str(root), "scope": scope.value})
        return self.locks_root / f"{identity}.lock"

    def _require_inactive(self, root: Path, action: str) -> None:
        if self.target_active(root):
            raise InstallationConflictError(
                f"Gemini extension target is active; stop it before {action}"
            )

    def _result(
        self,
        action: str,
        status: str,
        request: GeminiExtensionRequest,
        health: GeminiExtensionHealth,
    ) -> GeminiExtensionResult:
        return GeminiExtensionResult(
            action=action,
            status=status,
            extension_name=request.extension_name,
            package_id=request.package.id,
            version=health.version,
            scope=request.scope,
            enabled=health.enabled,
            restart_required=True,
        )
