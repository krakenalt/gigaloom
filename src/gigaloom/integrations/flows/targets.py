# ruff: noqa: E402, F401, F403, F405
"""Internal integration-flow targets."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .projection import *  # noqa: F403
from .resolution import *  # noqa: F403
from .state import *  # noqa: F403


class _FlowTargetsMixin:
    """Internal application-owned integration-flow operations."""

    def _apply_skill(
        self,
        request: Mapping[str, Any],
        resolved: _ResolvedPreview,
        *,
        authority: str,
        allow_user_home: bool,
    ) -> tuple[str, str]:
        skill = self._portable_skill_for_package(resolved.package)
        capability = self._skill_capability_provider(resolved.target.id)
        generated = generate_skill_package(skill, capability)
        install_request = build_skill_installation_request(
            resolved.package,
            skill,
            generated,
            scope=InstallationScope(request["scope"]),
            root=resolved.root,
        )
        installer = self._skill_installer(request, resolved.root)
        plan = installer.preview(install_request)
        if plan.plan_id != resolved.native_plan_id:
            raise InstallationConflictError("target preview changed before apply")
        result = installer.apply(
            install_request,
            plan,
            InstallationApproval(
                plan_id=plan.plan_id,
                authority=authority,
                allow_user_home=allow_user_home,
            ),
            verifier=generated_skill_verifier(generated),
        )
        discovery = discover_generated_skill(generated, resolved.root)
        if discovery.status.value != "discovered":
            raise IntegrationFlowError("skill discovery did not verify")
        return result.transaction_id, discovery.status.value

    def _apply_mcp(
        self,
        request: Mapping[str, Any],
        resolved: _ResolvedPreview,
        *,
        authority: str,
        allow_user_home: bool,
    ) -> tuple[str, str]:
        if (
            IntegrationFlowSource(request["source"])
            is IntegrationFlowSource.RAW_DESCRIPTOR
        ):
            if resolved.target.id == HARNESS_MANAGED_MCP_TARGET_ID:
                descriptor = self._raw_mcp_descriptor(request, resolved.root)
                plan = self._managed_mcp_inventory.preview(descriptor)
                if plan.plan_id != resolved.native_plan_id:
                    raise InstallationConflictError(
                        "target preview changed before apply"
                    )
                result = self._managed_mcp_inventory.apply(
                    descriptor, plan, authority=authority
                )
                verified = self._managed_mcp_inventory.verify(result.transaction_id)
                return verified.transaction_id, "inventory_verified"
            driver = self._mcp_driver_provider(resolved.target.id)
            native_request = self._raw_mcp_request(
                request,
                resolved.package,
                resolved.target.id,
                resolved.root,
                InstallationScope(request["scope"]),
            )
            plan = driver.preview_install(native_request)
            if plan.plan_id != resolved.native_plan_id:
                raise InstallationConflictError("target preview changed before apply")
            result = driver.install(
                native_request,
                plan,
                InstallationApproval(
                    plan_id=plan.plan_id,
                    authority=authority,
                    allow_user_home=allow_user_home,
                ),
            )
            health = driver.verify(result.transaction_id)
            if getattr(health, "status", None) != "healthy":
                raise IntegrationFlowError("native MCP discovery did not verify")
            return result.transaction_id, "native_verified"
        descriptor = self._external_mcp_descriptor(request)
        if resolved.target.id == HARNESS_MANAGED_MCP_TARGET_ID:
            plan = self._managed_mcp_inventory.preview(descriptor)
            if plan.plan_id != resolved.native_plan_id:
                raise InstallationConflictError("target preview changed before apply")
            result = self._managed_mcp_inventory.apply(
                descriptor, plan, authority=authority
            )
            verified = self._managed_mcp_inventory.verify(result.transaction_id)
            return verified.transaction_id, "inventory_verified"
        driver = self._mcp_driver_provider(resolved.target.id)
        native_request = self._mcp_request(
            descriptor,
            resolved.target.id,
            resolved.root,
            InstallationScope(request["scope"]),
        )
        plan = driver.preview_install(native_request)
        if plan.plan_id != resolved.native_plan_id:
            raise InstallationConflictError("target preview changed before apply")
        result = driver.install(
            native_request,
            plan,
            InstallationApproval(
                plan_id=plan.plan_id,
                authority=authority,
                allow_user_home=allow_user_home,
            ),
        )
        health = driver.verify(result.transaction_id)
        if getattr(health, "status", None) != "healthy":
            raise IntegrationFlowError("native MCP discovery did not verify")
        return result.transaction_id, "native_verified"

    def _rollback_mcp(
        self, record: IntegrationFlowRecord, resolved: _ResolvedPreview
    ) -> None:
        if record.receipt_id is None:
            raise IntegrationFlowConflictError("MCP flow receipt is missing")
        if resolved.target.id == HARNESS_MANAGED_MCP_TARGET_ID:
            self._managed_mcp_inventory.rollback(record.receipt_id)
            return
        self._mcp_driver_provider(resolved.target.id).rollback(record.receipt_id)

    def _apply_plugin(
        self,
        request: Mapping[str, Any],
        resolved: _ResolvedPreview,
        *,
        authority: str,
        allow_network: bool,
        allow_user_home: bool,
        native_consent_acknowledged: bool,
    ) -> tuple[str, str]:
        scope = InstallationScope(request["scope"])
        driver = self._plugin_driver(resolved.target.id, resolved.root, scope)
        native_request = self._plugin_request(
            request, resolved.package, resolved.target.id, resolved.root, scope
        )
        plan = driver.preview_install(native_request)
        if plan.plan_id != resolved.native_plan_id:
            raise InstallationConflictError("target preview changed before apply")
        if resolved.target.id == CODEX_PLUGIN_TARGET_ID:
            driver.install(
                native_request,
                plan,
                CodexPluginApproval(
                    plan_id=plan.plan_id,
                    authority=authority,
                    native_consent_acknowledged=native_consent_acknowledged,
                    allow_network=allow_network,
                    allow_user_home=allow_user_home,
                ),
            )
        elif resolved.target.id == CLAUDE_PLUGIN_TARGET_ID:
            driver.install(
                native_request,
                plan,
                ClaudePluginApproval(
                    plan_id=plan.plan_id,
                    authority=authority,
                    native_consent_acknowledged=native_consent_acknowledged,
                    allow_network=allow_network,
                    allow_user_home=allow_user_home,
                ),
            )
        else:
            result = driver.install(
                native_request,
                plan,
                GeminiExtensionApproval(
                    plan_id=plan.plan_id,
                    authority=authority,
                    native_consent_acknowledged=native_consent_acknowledged,
                    source_trust_acknowledged=native_consent_acknowledged,
                    allow_network=allow_network,
                    allow_user_home=allow_user_home,
                ),
            )
            if isinstance(result, GeminiExtensionHandoff):
                raise IntegrationFlowError("Gemini gallery requires provider handoff")
        health = driver.verify(native_request)
        if getattr(health, "status", None) != "healthy":
            raise IntegrationFlowError("native Plugin discovery did not verify")
        return str(resolved.native_plan_id), "native_verified"

    def _external_mcp_descriptor(
        self, request: Mapping[str, Any]
    ) -> ExternalMCPDescriptor:
        if (
            IntegrationFlowSource(request["source"])
            is not IntegrationFlowSource.CATALOG
        ):
            raise ValueError("managed external MCP execution requires a catalog pin")
        catalog_id = str(request.get("catalog_id") or "")
        entry = self.catalog.get(catalog_id) if catalog_id else None
        if entry is None or entry.mcp_response is None:
            raise ValueError("external MCP execution requires an official Registry pin")
        configuration = request.get("configuration")
        if not isinstance(configuration, Mapping):
            raise ValueError("external MCP configuration is invalid")
        selection = external_mcp_selection_from_dict(configuration.get("selection"))
        discovery = None
        discovery_id = configuration.get("discovery_catalog_id")
        if discovery_id is not None:
            discovery = self.catalog.get(str(discovery_id))
            if discovery is None:
                raise ValueError("external MCP discovery entry was not found")
        return normalize_external_mcp_candidate(
            entry,
            selection,
            discovery_entry=discovery,
        )

    def _mcp_request(
        self,
        descriptor: ExternalMCPDescriptor,
        target_id: str,
        root: Path,
        scope: InstallationScope,
    ) -> CodexMCPRequest | ClaudeMCPRequest | GeminiMCPRequest:
        package = descriptor.to_integration_package()
        spec = external_mcp_server_spec(descriptor, target_id)
        if target_id == CODEX_MCP_TARGET_ID:
            return CodexMCPRequest(package=package, scope=scope, root=root, server=spec)
        if target_id == CLAUDE_MCP_TARGET_ID:
            return ClaudeMCPRequest(
                package=package, scope=scope, root=root, server=spec
            )
        if target_id == GEMINI_MCP_TARGET_ID:
            return GeminiMCPRequest(
                package=package, scope=scope, root=root, server=spec
            )
        raise ValueError("external MCP native target is unsupported")

    def _raw_mcp_request(
        self,
        request: Mapping[str, Any],
        package: IntegrationPackage,
        target_id: str,
        root: Path,
        scope: InstallationScope,
    ) -> CodexMCPRequest | ClaudeMCPRequest | GeminiMCPRequest:
        descriptor = self._raw_mcp_descriptor(request, root)
        spec = external_mcp_server_spec(descriptor, target_id)
        if target_id == CODEX_MCP_TARGET_ID:
            return CodexMCPRequest(package=package, scope=scope, root=root, server=spec)
        if target_id == CLAUDE_MCP_TARGET_ID:
            return ClaudeMCPRequest(
                package=package, scope=scope, root=root, server=spec
            )
        if target_id == GEMINI_MCP_TARGET_ID:
            return GeminiMCPRequest(
                package=package, scope=scope, root=root, server=spec
            )
        raise ValueError("raw MCP native target is unsupported")

    def _raw_mcp_descriptor(
        self,
        request: Mapping[str, Any],
        root: Path,
    ) -> ExternalMCPDescriptor:
        configuration = request.get("configuration")
        if not isinstance(configuration, Mapping):
            raise ValueError("raw MCP configuration is invalid")
        target_id = str(request.get("target_id") or "")
        authored = mcp_authoring_configuration_from_dict(
            configuration,
            target_id=target_id,
        )
        package_id = str(request.get("package_id") or "")
        transport = (
            MCPTransport.STDIO
            if authored.transport is MCPAuthoringTransport.STDIO
            else MCPTransport.SSE
            if authored.transport is MCPAuthoringTransport.SSE
            else MCPTransport.STREAMABLE_HTTP
        )
        cwd = resolve_mcp_authoring_cwd(root, authored.cwd)
        content_hash = hashlib.sha256(
            json.dumps(
                {
                    "id": package_id,
                    "configuration": authored.to_dict(),
                    "cwd": cwd,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        network_origins = ()
        if authored.url is not None:
            parsed = urlsplit(authored.url)
            network_origins = (urlunsplit((parsed.scheme, parsed.netloc, "", "", "")),)
        return ExternalMCPDescriptor(
            id=package_id,
            official_name=package_id,
            version="0.0.0",
            title=package_id,
            description="Operator-reviewed MCP server",
            catalog_id=f"raw:{package_id}",
            immutable_ref=f"sha256:{content_hash}",
            content_hash=content_hash,
            transport=transport,
            command=authored.executable,
            args=authored.argv,
            cwd=cwd,
            url=authored.url,
            environment=authored.environment,
            headers=authored.headers,
            artifact=None,
            network_origins=network_origins,
            timeout_seconds=10,
            tool_policy=ExternalMCPToolPolicy(),
        )

    def _plugin_request(
        self,
        request: Mapping[str, Any],
        package: IntegrationPackage,
        target_id: str,
        root: Path,
        scope: InstallationScope,
    ) -> CodexPluginRequest | ClaudePluginRequest | GeminiExtensionRequest:
        configuration = request.get("configuration")
        if not isinstance(configuration, Mapping):
            raise ValueError("Plugin configuration is invalid")
        name = _plugin_name(package, configuration.get("plugin_name"))
        sparse_value = configuration.get("sparse", ())
        if not isinstance(sparse_value, (list, tuple)):
            raise ValueError("Plugin sparse paths must be a list")
        sparse = tuple(str(item) for item in sparse_value)
        is_local = package.source_type is IntegrationSourceType.LOCAL
        if target_id == CODEX_PLUGIN_TARGET_ID:
            source = CodexPluginSource(
                marketplace_name=name,
                kind=(
                    CodexPluginSourceKind.LOCAL
                    if is_local
                    else CodexPluginSourceKind.GIT
                ),
                location=package.source,
                ref=None if is_local else package.immutable_ref,
                sparse=() if is_local else sparse,
            )
            return CodexPluginRequest(package, scope, root, source, name)
        if target_id == CLAUDE_PLUGIN_TARGET_ID:
            source = ClaudePluginSource(
                marketplace_name=name,
                kind=(
                    ClaudePluginSourceKind.LOCAL
                    if is_local
                    else ClaudePluginSourceKind.GIT
                ),
                location=package.source,
                ref=None if is_local else package.immutable_ref,
                sparse=() if is_local else sparse,
            )
            return ClaudePluginRequest(package, scope, root, source, name)
        if target_id == GEMINI_EXTENSION_TARGET_ID:
            source = GeminiExtensionSource(
                kind=(
                    GeminiExtensionSourceKind.LOCAL
                    if is_local
                    else GeminiExtensionSourceKind.GALLERY
                    if package.source_type is IntegrationSourceType.PROVIDER_MARKETPLACE
                    else GeminiExtensionSourceKind.GIT
                ),
                location=package.source,
                ref=(
                    package.immutable_ref
                    if package.source_type is IntegrationSourceType.GIT
                    else None
                ),
            )
            return GeminiExtensionRequest(package, scope, root, source, name)
        raise ValueError("Plugin native target is unsupported")

    def _plugin_driver(
        self, target_id: str, root: Path, scope: InstallationScope
    ) -> Any:
        if self._plugin_driver_provider is not None:
            return self._plugin_driver_provider(target_id, root, scope)
        managed_roots = (root,) if scope is InstallationScope.MANAGED_HOME else ()
        project_roots = (root,) if scope is InstallationScope.PROJECT else ()
        if target_id == CODEX_PLUGIN_TARGET_ID:
            return CodexPluginTargetDriver(
                self.data_dir,
                managed_roots=managed_roots,
                source_roots=project_roots,
            )
        if target_id == CLAUDE_PLUGIN_TARGET_ID:
            return ClaudePluginTargetDriver(
                self.data_dir,
                managed_roots=managed_roots,
                project_roots=project_roots,
                source_roots=project_roots,
            )
        if target_id == GEMINI_EXTENSION_TARGET_ID:
            return GeminiExtensionTargetDriver(
                self.data_dir,
                managed_roots=managed_roots,
                project_roots=project_roots,
                source_roots=project_roots,
            )
        raise ValueError("Plugin native target is unsupported")

    def _default_mcp_driver(self, target_id: str) -> Any:
        if target_id == CODEX_MCP_TARGET_ID:
            return CodexMCPTargetDriver(self.data_dir)
        if target_id == CLAUDE_MCP_TARGET_ID:
            return ClaudeMCPTargetDriver(self.data_dir)
        if target_id == GEMINI_MCP_TARGET_ID:
            return GeminiMCPTargetDriver(self.data_dir)
        raise ValueError("external MCP native target is unsupported")

    def _skill_installer(
        self,
        request: Mapping[str, Any],
        root: Path,
    ) -> TransactionalIntegrationInstaller:
        scope = InstallationScope(request["scope"])
        return TransactionalIntegrationInstaller(
            self.data_dir,
            project_roots=(root,) if scope is InstallationScope.PROJECT else (),
        )

    def _target_root(
        self,
        request: Mapping[str, Any],
        target: ExtensionTargetDescriptor,
        *,
        create: bool,
    ) -> Path:
        scope = InstallationScope(request["scope"])
        if scope is InstallationScope.USER_HOME:
            raise ValueError(
                "user-home flows require an explicitly configured exact root"
            )
        if scope is InstallationScope.PROJECT:
            workspace = request.get("workspace")
            if not isinstance(workspace, str) or not workspace.strip():
                raise ValueError("project scope requires an explicit workspace")
            root = Path(workspace).expanduser().resolve()
            if not root.is_dir() or root.is_symlink():
                raise ValueError("project workspace must be an existing safe directory")
            return root
        package_hint = str(
            request.get("catalog_id") or request.get("package_id") or "candidate"
        )
        package_key = hashlib.sha256(package_hint.encode("utf-8")).hexdigest()[:24]
        root = self.data_dir / "native" / target.id / "homes" / package_key
        if create:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(root, 0o700)
        return root
