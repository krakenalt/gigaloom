"""Gemini provider-specific target driver."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import tempfile

from gpt2giga_harness.integration_installer import (
    InstallationConflictError,
)
from gpt2giga_harness.integration_packages import (
    InstallationScope,
)
from gpt2giga_harness.harnesses.ports import exclusive_file_lock
from gpt2giga_harness.types import redact_secrets


from gpt2giga_harness.harnesses.builtins.gemini.target_contracts import (
    GEMINI_EXTENSION_TARGET_DESCRIPTOR,
    GeminiExtensionApproval,
    GeminiExtensionCommandError,
    GeminiExtensionCommandRunner,
    GeminiExtensionHandoff,
    GeminiExtensionHealth,
    GeminiExtensionInstallation,
    GeminiExtensionPlan,
    GeminiExtensionPolicy,
    GeminiExtensionProbe,
    GeminiExtensionRequest,
    GeminiExtensionResult,
    GeminiExtensionSourceKind,
    _GeminiExecutionContext,
    _absolute_path,
)
from gpt2giga_harness.harnesses.builtins.gemini.target_source import (
    _bounded_output,
    _first_line,
    _installation_from_item,
    _is_relative_to,
    _native_source_matches,
    _normalize_roots,
    _required_string,
    _run_command,
    _supported_gemini_version,
)
from gpt2giga_harness.harnesses.builtins.gemini.target_operations import (
    _GeminiTargetOperations,
)


class GeminiExtensionTargetDriver(_GeminiTargetOperations):
    """Operate Gemini extensions only through documented native CLI commands."""

    descriptor = GEMINI_EXTENSION_TARGET_DESCRIPTOR

    def __init__(
        self,
        data_dir: str | Path,
        *,
        managed_roots: Sequence[str | Path] = (),
        project_roots: Sequence[str | Path] = (),
        source_roots: Sequence[str | Path] = (),
        user_home_root: str | Path | None = None,
        allow_user_home: bool = False,
        executable: Sequence[str] = ("gemini",),
        command_runner: GeminiExtensionCommandRunner | None = None,
        policy: GeminiExtensionPolicy | None = None,
        target_active: Callable[[Path], bool] | None = None,
    ) -> None:
        self.data_dir = _absolute_path(Path(data_dir))
        self.locks_root = self.data_dir / "integrations" / "gemini-extension" / "locks"
        native_root = self.data_dir / "native"
        self.managed_roots = _normalize_roots(managed_roots)
        for root in self.managed_roots:
            if not _is_relative_to(root, native_root):
                raise ValueError(
                    "managed Gemini extension roots must be Harness-native"
                )
        self.project_roots = _normalize_roots(project_roots)
        self.source_roots = _normalize_roots(source_roots)
        self.user_home_root = (
            _absolute_path(Path(user_home_root)) if user_home_root is not None else None
        )
        self.allow_user_home = allow_user_home
        self.executable = tuple(str(item) for item in executable)
        if not self.executable or any(not item for item in self.executable):
            raise ValueError("Gemini extension executable is invalid")
        self.command_runner = command_runner or _run_command
        self.policy = policy or (lambda _action, _package, _scope: True)
        if not callable(self.policy):
            raise TypeError("Gemini extension policy must be callable")
        self.target_active = target_active or (lambda _root: False)

    def probe_target(self) -> GeminiExtensionProbe:
        """Probe documented extension surfaces in an isolated temporary home."""
        with tempfile.TemporaryDirectory(
            prefix="gigaloom-gemini-extension-probe-"
        ) as raw:
            home = Path(raw)
            context = _GeminiExecutionContext(home, home, "user")
            commands = (
                ("--version",),
                ("extensions", "--help"),
                ("extensions", "validate", "--help"),
                ("extensions", "install", "--help"),
                ("extensions", "list", "--help"),
                ("extensions", "update", "--help"),
                ("extensions", "uninstall", "--help"),
                ("extensions", "enable", "--help"),
                ("extensions", "disable", "--help"),
            )
            results = tuple(self._run(context, args) for args in commands)
        version = _first_line(results[0].stdout or results[0].stderr)
        texts = tuple(_bounded_output(item) for item in results[1:])
        expectations = (
            ("extension_validate", "validate <path>", texts[1]),
            ("extension_install_ref", "--ref", texts[2]),
            ("extension_install_consent", "--consent", texts[2]),
            ("extension_list_json", "--output-format", texts[3]),
            ("extension_update", "--all", texts[4]),
            ("extension_uninstall", "--all", texts[5]),
            ("extension_enable_scope", "--scope", texts[6]),
            ("extension_disable_scope", "--scope", texts[7]),
        )
        capabilities = tuple(
            name for name, needle, value in expectations if needle in value
        )
        supported = (
            all(item.returncode == 0 for item in results)
            and len(capabilities) == len(expectations)
            and _supported_gemini_version(version)
        )
        return GeminiExtensionProbe(
            status="supported" if supported else "unsupported",
            version=version,
            command=str(redact_secrets(self.executable[0])),
            capabilities=capabilities,
            gallery_automation="provider_handoff_required",
            evidence="bounded --version and documented extensions help probes",
        )

    def discover_installed(self) -> tuple[GeminiExtensionInstallation, ...]:
        """Discover extensions through native JSON for all admitted roots."""
        contexts: list[tuple[InstallationScope, Path, _GeminiExecutionContext]] = []
        contexts.extend(
            (
                InstallationScope.MANAGED_HOME,
                root,
                self._context(root, InstallationScope.MANAGED_HOME),
            )
            for root in self.managed_roots
            if root.is_dir() and not root.is_symlink()
        )
        contexts.extend(
            (
                InstallationScope.PROJECT,
                root,
                self._context(root, InstallationScope.PROJECT),
            )
            for root in self.project_roots
            if root.is_dir() and not root.is_symlink()
        )
        if (
            self.allow_user_home
            and self.user_home_root is not None
            and self.user_home_root.is_dir()
        ):
            contexts.append(
                (
                    InstallationScope.USER_HOME,
                    self.user_home_root,
                    self._context(self.user_home_root, InstallationScope.USER_HOME),
                )
            )
        found: list[GeminiExtensionInstallation] = []
        for scope, root, context in contexts:
            for item in self._list(context):
                found.append(_installation_from_item(item, scope=scope, root=root))
        return tuple(sorted(found, key=lambda item: (str(item.root), item.name)))

    def preview_install(self, request: GeminiExtensionRequest) -> GeminiExtensionPlan:
        """Preview documented native installation or an explicit gallery handoff."""
        return self._preview(request, action="install")

    def install(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
    ) -> GeminiExtensionResult | GeminiExtensionHandoff:
        """Install through Gemini CLI, or return a visible gallery handoff."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            if plan.policy_status == "provider_handoff_required":
                self._authorize_handoff(request, plan, approval, action="install")
                return self._gallery_handoff(request)
            self._authorize(request, plan, approval, action="install")
            self._require_inactive(root, "install")
            args = ["extensions", "install", request.source.location]
            if request.source.kind is GeminiExtensionSourceKind.GIT:
                args.extend(("--ref", str(request.source.ref)))
            args.extend(("--consent", "--skip-settings"))
            try:
                self._command(context, tuple(args), trust_workspace=True)
                health = self.verify(request)
                if health.status != "healthy":
                    raise GeminiExtensionCommandError(
                        "Gemini extension install did not produce exact native discovery"
                    )
            except Exception:
                self._best_effort_uninstall(context, request.extension_name)
                raise
        return self._result("install", "installed", request, health)

    def verify(self, request: GeminiExtensionRequest) -> GeminiExtensionHealth:
        """Verify exact identity through native JSON without reading CLI caches."""
        _root, context = self._admit(request)
        current = self._current(context, request.extension_name)
        if current is None:
            return GeminiExtensionHealth(
                extension_name=request.extension_name,
                package_id=request.package.id,
                version="unknown",
                enabled=False,
                exact_version=False,
                exact_source=False,
                status="missing",
            )
        version = _required_string(current, "version", "Gemini extension version")
        exact_version = version == request.package.version
        exact_source = _native_source_matches(current, request.source)
        enabled = current.get("isActive") is True
        return GeminiExtensionHealth(
            extension_name=request.extension_name,
            package_id=request.package.id,
            version=version,
            enabled=enabled,
            exact_version=exact_version,
            exact_source=exact_source,
            status="healthy" if exact_version and exact_source else "degraded",
        )

    def health(self, request: GeminiExtensionRequest) -> GeminiExtensionHealth:
        """Alias native exact-discovery verification."""
        return self.verify(request)

    def preview_enable(self, request: GeminiExtensionRequest) -> GeminiExtensionPlan:
        """Preview native extension enablement."""
        return self._preview(request, action="enable")

    def enable(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
    ) -> GeminiExtensionResult:
        """Enable through the documented native CLI after exact approval."""
        return self._set_enabled(request, plan, approval, enabled=True)

    def preview_disable(self, request: GeminiExtensionRequest) -> GeminiExtensionPlan:
        """Preview native extension disablement."""
        return self._preview(request, action="disable")

    def disable(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
    ) -> GeminiExtensionResult:
        """Disable through the documented native CLI after exact approval."""
        return self._set_enabled(request, plan, approval, enabled=False)

    def preview_update(self, request: GeminiExtensionRequest) -> GeminiExtensionPlan:
        """Preview a reviewed source refresh or native handoff."""
        return self._preview(request, action="update")

    def update(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
    ) -> GeminiExtensionResult | GeminiExtensionHandoff:
        """Update local sources; hand immutable Git replacement to Gemini."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            if plan.policy_status == "provider_handoff_required":
                self._authorize_handoff(request, plan, approval, action="update")
                return GeminiExtensionHandoff(
                    action="update",
                    extension_name=request.extension_name,
                    command=self.executable
                    + (
                        "extensions",
                        "install",
                        request.source.location,
                        "--ref",
                        str(request.source.ref),
                    ),
                    interaction=(
                        "review the immutable replacement, uninstall the current "
                        "extension, then install the selected ref"
                    ),
                    consent_owner="gemini_cli",
                    restart_required=True,
                    reason=(
                        "Gemini exposes ref selection only during install, not as an "
                        "atomic version-selecting update"
                    ),
                )
            self._authorize(request, plan, approval, action="update")
            self._require_inactive(root, "update")
            self._command(context, ("extensions", "update", request.extension_name))
            health = self.verify(request)
            if health.status != "healthy":
                raise GeminiExtensionCommandError(
                    "Gemini extension update did not produce exact native discovery"
                )
        return self._result("update", "updated", request, health)

    def preview_uninstall(self, request: GeminiExtensionRequest) -> GeminiExtensionPlan:
        """Preview exact native removal."""
        return self._preview(request, action="uninstall")

    def uninstall(
        self,
        request: GeminiExtensionRequest,
        plan: GeminiExtensionPlan,
        approval: GeminiExtensionApproval,
    ) -> GeminiExtensionResult:
        """Remove one extension through the documented native CLI."""
        root, context = self._admit(request)
        with exclusive_file_lock(self._lock_path(root, request.scope)):
            self._authorize(request, plan, approval, action="uninstall")
            self._require_inactive(root, "uninstall")
            self._command(
                context,
                ("extensions", "uninstall", request.extension_name),
            )
            if self._current(context, request.extension_name) is not None:
                raise GeminiExtensionCommandError(
                    "Gemini extension uninstall did not remove native discovery"
                )
        return GeminiExtensionResult(
            action="uninstall",
            status="uninstalled",
            extension_name=request.extension_name,
            package_id=request.package.id,
            version=None,
            scope=request.scope,
            enabled=False,
            restart_required=True,
        )

    def rollback(self, request: GeminiExtensionRequest) -> GeminiExtensionHandoff:
        """Expose truthful reviewed-source rollback when no atomic CLI exists."""
        if self.verify(request).status == "missing":
            raise InstallationConflictError(
                "Gemini extension rollback requires a native installation"
            )
        source_args: tuple[str, ...] = (request.source.location,)
        if request.source.ref is not None:
            source_args += ("--ref", request.source.ref)
        return GeminiExtensionHandoff(
            action="rollback",
            extension_name=request.extension_name,
            command=self.executable + ("extensions", "install") + source_args,
            interaction="restore the previously reviewed source, then reinstall it",
            consent_owner="gemini_cli",
            restart_required=True,
            reason="Gemini exposes no atomic version-selecting rollback command",
        )

    def gallery_handoff(
        self, request: GeminiExtensionRequest
    ) -> GeminiExtensionHandoff:
        """Return the visible gallery-to-reviewed-source transition."""
        self._admit(request)
        if request.source.kind is not GeminiExtensionSourceKind.GALLERY:
            raise ValueError("Gemini gallery handoff requires a gallery source")
        return self._gallery_handoff(request)
