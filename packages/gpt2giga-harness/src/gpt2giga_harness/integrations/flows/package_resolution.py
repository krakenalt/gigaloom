# ruff: noqa: E402, F401, F403, F405
"""Internal integration flow implementation slice."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .projection import *  # noqa: F403
from .resolution import *  # noqa: F403
from .state import *  # noqa: F403


class _FlowResolutionMixin:
    """Implementation slice for application-owned integration flows."""

    def _resolve_preview(
        self,
        request: Mapping[str, Any],
        *,
        existing: bool = False,
    ) -> _ResolvedPreview:
        source = IntegrationFlowSource(request["source"])
        target = _target(str(request["target_id"]))
        scope = InstallationScope(request["scope"])
        if scope not in target.scopes:
            raise ValueError("selected target does not support the requested scope")
        root = self._target_root(request, target, create=not existing)
        package = self._resolve_package(request, source, target, scope)
        source_provenance = self._source_provenance(request)
        _validate_target_compatibility(package, target, scope)
        if target.id in _SKILL_TARGETS:
            skill = self._portable_skill_for_package(package)
            capability = self._skill_capability_provider(target.id)
            generated = generate_skill_package(skill, capability)
            install_request = build_skill_installation_request(
                package,
                skill,
                generated,
                scope=scope,
                root=root,
            )
            installer = self._skill_installer(request, root)
            native = installer.preview(install_request)
            return _ResolvedPreview(
                package=package,
                target=target,
                root=root,
                executable=True,
                execution_owner="workbench_transactional_installer",
                native_plan_id=native.plan_id,
                configuration_diff=tuple(
                    f"{'update' if item.current_sha256 else 'create'}:{item.relative_path}"
                    for item in native.mutations
                ),
                restart_required=generated.restart_required,
                source_provenance=source_provenance,
            )
        if (
            target.id in _MCP_TARGET_IDS
            and source is IntegrationFlowSource.CATALOG
            and self.catalog.get(str(request.get("catalog_id") or "")) is not None
            and self.catalog.get(str(request.get("catalog_id") or "")).mcp_response
            is not None
        ):
            descriptor = self._external_mcp_descriptor(request)
            target_preview = project_external_mcp_target(descriptor, target.id)
            if not target_preview.supported:
                raise ValueError(
                    "external MCP target is incompatible: "
                    f"{target_preview.error_code or 'unknown'}"
                )
            if target.id == HARNESS_MANAGED_MCP_TARGET_ID:
                native = self._managed_mcp_inventory.preview(descriptor)
                configuration_diff = (
                    ("create:managed-mcp-inventory",) if native.changed else ()
                )
                native_plan_id = native.plan_id
                execution_owner = "harness_managed_mcp_inventory"
                restart_required = False
            else:
                driver = self._mcp_driver_provider(target.id)
                native_request = self._mcp_request(descriptor, target.id, root, scope)
                native = driver.preview_install(native_request)
                configuration_diff = tuple(
                    f"{'update' if item.current_sha256 else 'create'}:{item.relative_path}"
                    for item in native.installation.mutations
                )
                native_plan_id = native.plan_id
                execution_owner = "provider_native_target_driver"
                restart_required = True
            return _ResolvedPreview(
                package=descriptor.to_integration_package(),
                target=target,
                root=root,
                executable=True,
                execution_owner=execution_owner,
                native_plan_id=native_plan_id,
                configuration_diff=configuration_diff,
                restart_required=restart_required,
                source_provenance=source_provenance,
            )
        if (
            target.id in _MCP_TARGET_IDS
            and source is IntegrationFlowSource.RAW_DESCRIPTOR
        ):
            descriptor = self._raw_mcp_descriptor(request, root)
            target_preview = project_external_mcp_target(descriptor, target.id)
            if not target_preview.supported:
                raise ValueError(
                    "raw MCP target is incompatible: "
                    f"{target_preview.error_code or 'unknown'}"
                )
            if target.id == HARNESS_MANAGED_MCP_TARGET_ID:
                native = self._managed_mcp_inventory.preview(descriptor)
                configuration_diff = (
                    ("create:managed-mcp-inventory",) if native.changed else ()
                )
                native_plan_id = native.plan_id
                execution_owner = "harness_managed_mcp_inventory"
                restart_required = False
            else:
                driver = self._mcp_driver_provider(target.id)
                native = driver.preview_install(
                    self._raw_mcp_request(request, package, target.id, root, scope)
                )
                configuration_diff = tuple(
                    f"{'update' if item.current_sha256 else 'create'}:{item.relative_path}"
                    for item in native.installation.mutations
                )
                native_plan_id = native.plan_id
                execution_owner = "provider_native_target_driver"
                restart_required = True
            return _ResolvedPreview(
                package=package,
                target=target,
                root=root,
                executable=True,
                execution_owner=execution_owner,
                native_plan_id=native_plan_id,
                configuration_diff=configuration_diff,
                restart_required=restart_required,
                configuration_preview={
                    "transport": descriptor.transport.value,
                    "target": dict(target_preview.configuration),
                    "secret_references": list(target_preview.secret_references),
                },
                source_provenance=source_provenance,
            )
        if target.id in _PLUGIN_TARGET_IDS:
            driver = self._plugin_driver(target.id, root, scope)
            native_request = self._plugin_request(
                request, package, target.id, root, scope
            )
            native = driver.preview_install(native_request)
            return _ResolvedPreview(
                package=package,
                target=target,
                root=root,
                executable=True,
                execution_owner="provider_native_target_driver",
                native_plan_id=native.plan_id,
                configuration_diff=tuple(
                    f"native-command:{item}"
                    for item in getattr(native, "command_ids", ())
                ),
                restart_required=bool(getattr(native, "restart_required", True)),
                source_provenance=source_provenance,
            )
        return _ResolvedPreview(
            package=package,
            target=target,
            root=root,
            executable=False,
            execution_owner=_execution_owner(target.id),
            native_plan_id=None,
            configuration_diff=_configuration_diff(request.get("configuration", {})),
            restart_required=True,
            handoff_reason=_handoff_reason(target.id),
            source_provenance=source_provenance,
        )

    def _source_provenance(
        self, request: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        if request.get("source") != IntegrationFlowSource.CATALOG.value:
            return None
        catalog_id = str(request.get("catalog_id") or "")
        entry = self.catalog.get(catalog_id) if catalog_id else None
        if entry is None or entry.federated is None:
            return None
        metadata = entry.federated
        return {
            "canonical_source": entry.source_id,
            "upstream_id": metadata.upstream_id,
            "canonical_origin": metadata.canonical_origin,
            "repository_url": metadata.artifact_url,
            "artifact_url": metadata.artifact_url,
            "immutable_ref": metadata.immutable_ref or entry.immutable_ref,
            "content_hash": metadata.content_hash or entry.content_hash,
            "relative_path": metadata.relative_path,
            "discovery_location": metadata.discovery_location
            or f"{entry.source_id}/{metadata.upstream_id}",
            "observed_at": metadata.observed_at or entry.last_seen_at,
        }

    def _resolve_package(
        self,
        request: Mapping[str, Any],
        source: IntegrationFlowSource,
        target: ExtensionTargetDescriptor,
        scope: InstallationScope,
    ) -> IntegrationPackage:
        if source is IntegrationFlowSource.CATALOG:
            self._ensure_catalog_seeded()
            catalog_id = str(request.get("catalog_id") or "")
            entry = self.catalog.get(catalog_id) if catalog_id else None
            if entry is None:
                raise ValueError("catalog selection requires an exact package entry")
            if entry.package is not None:
                return entry.package
            if entry.mcp_response is not None:
                descriptor = self._external_mcp_descriptor(request)
                preview = project_external_mcp_target(descriptor, target.id)
                if not preview.supported:
                    raise ValueError(
                        "external MCP target is incompatible: "
                        f"{preview.error_code or 'unknown'}"
                    )
                return descriptor.to_integration_package()
            raise ValueError("catalog selection requires an exact package entry")
        if source is IntegrationFlowSource.RAW_DESCRIPTOR:
            return _raw_mcp_package(request, target, scope)
        manifest = request.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("selected source requires an exact package manifest")
        package = integration_package_from_dict(manifest)
        expected = {
            IntegrationFlowSource.MARKETPLACE: IntegrationSourceType.PROVIDER_MARKETPLACE,
            IntegrationFlowSource.GIT: IntegrationSourceType.GIT,
            IntegrationFlowSource.LOCAL: IntegrationSourceType.LOCAL,
            IntegrationFlowSource.PACKAGE: IntegrationSourceType.PACKAGE,
        }[source]
        if package.source_type is not expected:
            raise ValueError("package source_type does not match the selected source")
        return package

    def _catalog_entry_for_package(self, package: IntegrationPackage) -> CatalogEntry:
        self._ensure_catalog_seeded()
        entry = next(
            (
                item
                for item in self.catalog.list()
                if item.package_id == package.id and item.version == package.version
            ),
            None,
        )
        if entry is None or entry.package != package:
            raise ValueError("portable skill execution requires an exact catalog pin")
        return entry

    def _portable_skill_for_package(self, package: IntegrationPackage):
        entry = self._catalog_entry_for_package(package)
        if entry.source_id == BUILTIN_SKILL_SOURCE_ID:
            return get_builtin_skill_bundle(package.id).skill
        digest = package.checksum.removeprefix("sha256:")
        try:
            artifact = self._external_skill_store.resolve(digest)
            return parse_external_skill(artifact)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "external Skill artifact is unavailable or drifted"
            ) from exc
