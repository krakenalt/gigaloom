# ruff: noqa: E402, F401, F403, F405
"""Integration package and catalog resolution helpers."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .state import *  # noqa: F403


def _raw_mcp_package(
    request: Mapping[str, Any],
    target: ExtensionTargetDescriptor,
    scope: InstallationScope,
) -> IntegrationPackage:
    if IntegrationComponentType.MCP not in target.component_types:
        raise ValueError("raw descriptors are supported only by MCP targets")
    package_id = str(request.get("package_id") or "")
    _validate_identity(package_id, field_name="raw descriptor package id")
    configuration = request.get("configuration")
    if not isinstance(configuration, Mapping):
        raise ValueError("raw descriptor configuration is required")
    authored = mcp_authoring_configuration_from_dict(
        configuration,
        target_id=target.id,
    )
    canonical = authored.to_dict()
    descriptor_hash = _json_hash(canonical)
    requirements: list[IntegrationRequirement] = []
    if authored.transport is MCPAuthoringTransport.STDIO:
        requirements.append(
            IntegrationRequirement(
                id="mcp-command",
                type=IntegrationRequirementType.COMMAND,
                classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                reason="Start the reviewed MCP server command.",
                argv=(str(authored.executable), *authored.argv),
                environment=tuple(authored.environment),
            )
        )
    else:
        parsed_url = urlsplit(str(authored.url))
        origin = urlunsplit((parsed_url.scheme, parsed_url.netloc, "", "", ""))
        requirements.append(
            IntegrationRequirement(
                id="mcp-network",
                type=IntegrationRequirementType.NETWORK,
                classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                reason="Connect to the reviewed MCP server origin.",
                locator=origin,
            )
        )
    secret_names = sorted(set(authored.environment) | set(authored.headers))
    for index, _name in enumerate(secret_names):
        requirements.append(
            IntegrationRequirement(
                id=f"mcp-secret-{index + 1}",
                type=IntegrationRequirementType.SECRET,
                classification=IntegrationPolicyClass.EXPLICIT_APPROVAL,
                reason="Resolve a named environment reference at target runtime.",
                secret_owner="environment",
            )
        )
    return IntegrationPackage(
        id=package_id,
        version=f"raw-{descriptor_hash[:12]}",
        publisher="local-operator",
        license="NOASSERTION",
        source_type=IntegrationSourceType.RAW_MCP,
        source=f"raw-mcp://{package_id}",
        immutable_ref=f"descriptor:{descriptor_hash}",
        checksum=f"sha256:{descriptor_hash}",
        components=(
            IntegrationComponent(
                id=f"{package_id}-mcp",
                type=IntegrationComponentType.MCP,
                portable=True,
            ),
        ),
        requirements=tuple(requirements),
        overlays=(
            IntegrationTargetOverlay(
                target_id=target.id,
                component_ids=(f"{package_id}-mcp",),
                requirement_ids=tuple(item.id for item in requirements),
            ),
        ),
        compatibility=(IntegrationCompatibility(target_id=target.id),),
        scopes=(scope,),
        update_policy=IntegrationUpdatePolicy.PINNED,
        verification_steps=("native-discovery",),
        rollback_steps=("restore-target-snapshot",),
    )


def _validate_target_compatibility(
    package: IntegrationPackage,
    target: ExtensionTargetDescriptor,
    scope: InstallationScope,
) -> None:
    if scope not in package.scopes:
        raise ValueError("package does not support the selected scope")
    if not any(item.target_id == target.id for item in package.compatibility):
        raise ValueError("package is not compatible with the selected target")
    component_types = {item.type for item in package.components}
    if not component_types & set(target.component_types):
        raise ValueError("package has no component supported by the selected target")
    assessment = assess_integration_package(package)
    if assessment.decision is IntegrationTrustDecision.BLOCKED:
        raise ValueError("package trust assessment is blocked")


def _plugin_name(package: IntegrationPackage, configured: Any) -> str:
    raw = str(configured or "")
    if not raw:
        component = next(
            (
                item
                for item in package.components
                if item.type
                in {IntegrationComponentType.PLUGIN, IntegrationComponentType.EXTENSION}
            ),
            None,
        )
        raw = component.id if component is not None else package.id
    normalized = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-")
    if not normalized:
        raise ValueError("Plugin name is invalid")
    return normalized[:64].rstrip("-")


def _catalog_entry_to_dict(entry: CatalogEntry) -> dict[str, Any]:
    package = entry.package
    federated = entry.federated
    return {
        "catalog_id": entry.catalog_id,
        "source_id": entry.source_id,
        "source_type": entry.source_type.value,
        "package_id": entry.package_id,
        "version": entry.version,
        "immutable_ref": entry.immutable_ref,
        "content_hash": entry.content_hash,
        "status": entry.status.value,
        "pinned": entry.pinned,
        "source_present": entry.source_present,
        "install_authorized": False,
        "trust_decision": entry.trust_decision.value,
        "component_types": (
            sorted({item.type.value for item in package.components})
            if package
            else (
                [federated.component]
                if federated is not None
                else (
                    [IntegrationComponentType.MCP.value] if entry.mcp_response else []
                )
            )
        ),
        "target_ids": (
            sorted(item.target_id for item in package.compatibility)
            if package
            else (sorted(_MCP_TARGET_IDS) if entry.mcp_response else [])
        ),
        "scopes": (
            [item.value for item in package.scopes]
            if package
            else ([InstallationScope.MANAGED_HOME.value] if entry.mcp_response else [])
        ),
        "discovery": (
            {
                "upstream_id": federated.upstream_id,
                "canonical_package_id": federated.canonical_package_id,
                "name": federated.name,
                "component": federated.component,
                "canonical_origin": federated.canonical_origin,
                "detail_url": federated.detail_url,
                "artifact_url": federated.artifact_url,
                "repository_url": federated.artifact_url,
                "observed_at": federated.observed_at,
                "discovery_location": federated.discovery_location,
                "immutable_ref": federated.immutable_ref,
                "content_hash": federated.content_hash,
                "relative_path": federated.relative_path,
                "curated": federated.curated,
                "popularity": federated.popularity,
                "upstream_audit": federated.upstream_audit,
                "artifact_resolved": federated.artifact_resolved,
                "source_present": federated.source_present,
                "install_authorized": False,
            }
            if federated is not None
            else None
        ),
    }


def _catalog_source_to_dict(item: Any, *, now: datetime) -> dict[str, Any]:
    last_success = (
        datetime.fromisoformat(item.last_success_at.replace("Z", "+00:00"))
        if item.last_success_at is not None
        else None
    )
    freshness = (
        datetime.fromisoformat(item.freshness_expires_at.replace("Z", "+00:00"))
        if item.freshness_expires_at is not None
        else None
    )
    stale = item.last_success_at is not None and (
        not item.last_attempt_succeeded
        or (
            item.source_type is CatalogSourceType.FEDERATED_CATALOG
            and (freshness is None or freshness <= now)
        )
    )
    return {
        "id": item.source_id,
        "status": (
            "unavailable"
            if item.last_success_at is None and not item.last_attempt_succeeded
            else "stale"
            if stale
            else "ready"
        ),
        "last_sync_at": item.last_success_at,
        "last_attempt_at": item.last_attempt_at,
        "cache_age_seconds": (
            max(0, int((now - last_success).total_seconds()))
            if last_success is not None
            else None
        ),
        "last_good": item.last_success_at is not None,
        "stale": stale,
        "reason_code": item.errors[-1].code if item.errors else None,
        "next_retry_at": item.next_retry_at,
        "entry_count": item.entry_count,
        "content_free": True,
    }


def _requirement_to_dict(item: IntegrationRequirement) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": item.type.value,
        "classification": item.classification.value,
        "reason": item.reason,
        "argv": list(item.argv),
        "locator": item.locator,
        "checksum": item.checksum,
        "secret_owner": item.secret_owner,
        "environment": list(item.environment),
    }


def _configuration_diff(configuration: object) -> tuple[str, ...]:
    if not isinstance(configuration, Mapping):
        return ()
    return tuple(f"set:{key}" for key in sorted(configuration))


def _execution_owner(target_id: str) -> str:
    if target_id in _SKILL_TARGETS:
        return "workbench_transactional_installer"
    if target_id == HARNESS_MANAGED_MCP_TARGET_ID:
        return "harness_managed_mcp_inventory"
    if target_id == HARNESS_PACKAGE_TARGET.id:
        return "python_package_manager_and_adapter_sdk"
    return "provider_native_target_driver"


def _handoff_reason(target_id: str) -> str:
    if target_id == HARNESS_PACKAGE_TARGET.id:
        return (
            "Package installation and SDK conformance remain explicit client handoffs."
        )
    return (
        "The exact provider-native target driver owns mutation; this application "
        "flow retains the approved preview and handoff without bypassing consent."
    )


def _target(target_id: str) -> ExtensionTargetDescriptor:
    target = next((item for item in BUILTIN_FLOW_TARGETS if item.id == target_id), None)
    if target is None:
        raise ValueError("unknown integration target")
    return target


__all__ = [name for name in globals() if not name.startswith("__")]
