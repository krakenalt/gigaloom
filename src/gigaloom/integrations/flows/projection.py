# ruff: noqa: E402, F401, F403, F405
"""Public and private integration flow projections."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .resolution import *  # noqa: F403
from .state import *  # noqa: F403


def integration_flow_record_to_dict(record: IntegrationFlowRecord) -> dict[str, Any]:
    """Return the bounded public lifecycle projection."""
    return {
        "id": record.id,
        "plan_id": record.plan_id,
        "status": record.status.value,
        "source": record.source.value,
        "package_id": record.package_id,
        "package_version": record.package_version,
        "manifest_sha256": record.manifest_sha256,
        "source_provenance": (
            dict(record.source_provenance)
            if record.source_provenance is not None
            else None
        ),
        "target_id": record.target_id,
        "scope": record.scope.value,
        "verification_status": record.verification_status,
        "rollback_available": record.rollback_available,
        "error_code": record.error_code,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "events": [asdict(item) for item in record.events],
        "content_free": True,
    }


def _public_plan(
    request: Mapping[str, Any], resolved: _ResolvedPreview
) -> dict[str, Any]:
    assessment = assess_integration_package(resolved.package)
    requirements = tuple(resolved.package.requirements)
    network_required = any(
        item.type is IntegrationRequirementType.NETWORK for item in requirements
    ) or IntegrationFlowSource(request["source"]) in {
        IntegrationFlowSource.GIT,
        IntegrationFlowSource.MARKETPLACE,
    }
    native_consent = resolved.target.id in {
        CODEX_MCP_TARGET_DESCRIPTOR.id,
        CLAUDE_MCP_TARGET_DESCRIPTOR.id,
        GEMINI_MCP_TARGET_DESCRIPTOR.id,
        CODEX_PLUGIN_TARGET_DESCRIPTOR.id,
        CLAUDE_PLUGIN_TARGET_DESCRIPTOR.id,
        GEMINI_EXTENSION_TARGET_DESCRIPTOR.id,
    }
    semantic = {
        "schema_version": INTEGRATION_FLOW_SCHEMA_VERSION,
        "source": request["source"],
        "catalog_id": request.get("catalog_id"),
        "manifest_sha256": integration_package_semantic_hash(resolved.package),
        "target_id": resolved.target.id,
        "target_revision": resolved.target.revision,
        "scope": request["scope"],
        "workspace": request.get("workspace"),
        "configuration_sha256": _json_hash(request.get("configuration", {})),
        "native_plan_id": resolved.native_plan_id,
        "source_provenance": resolved.source_provenance,
    }
    plan_id = f"plan_{_json_hash(semantic)}"
    return {
        "plan_id": plan_id,
        "package": {
            "id": resolved.package.id,
            "version": resolved.package.version,
            "publisher": resolved.package.publisher,
            "license": resolved.package.license,
            "source": resolved.package.source,
            "source_type": resolved.package.source_type.value,
            "immutable_ref": resolved.package.immutable_ref,
            "checksum": resolved.package.checksum,
            "manifest_sha256": integration_package_semantic_hash(resolved.package),
            "source_provenance": (
                dict(resolved.source_provenance)
                if resolved.source_provenance is not None
                else None
            ),
        },
        "target": {
            "id": resolved.target.id,
            "revision": resolved.target.revision,
            "scope": request["scope"],
            "execution_owner": resolved.execution_owner,
            "executable": resolved.executable,
        },
        "risk": {
            "decision": assessment.decision.value,
            "install_authorized": assessment.install_authorized,
            "diagnostics": [
                {
                    "code": item.code,
                    "subject_id": item.subject_id,
                    "classification": item.classification.value,
                }
                for item in assessment.diagnostics
            ],
        },
        "permissions": {
            "network": network_required,
            "native_consent": native_consent,
            "user_home": request["scope"] == InstallationScope.USER_HOME.value,
            "requirements": [_requirement_to_dict(item) for item in requirements],
        },
        "configuration": {
            "diff": list(resolved.configuration_diff),
            "restart_required": resolved.restart_required,
            "fields": sorted(request.get("configuration", {})),
            "preview": dict(resolved.configuration_preview or {}),
        },
        "verification_steps": list(resolved.package.verification_steps),
        "rollback_steps": list(resolved.package.rollback_steps),
        "handoff_reason": resolved.handoff_reason,
        "approval_required": True,
        "content_free": True,
    }


def _normalize_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("integration flow request must be an object")
    allowed = {
        "source",
        "catalog_id",
        "manifest",
        "target_id",
        "scope",
        "workspace",
        "package_id",
        "configuration",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError("integration flow request contains unknown fields")
    source = IntegrationFlowSource(str(payload.get("source") or ""))
    target_id = str(payload.get("target_id") or "")
    _target(target_id)
    scope = InstallationScope(str(payload.get("scope") or ""))
    catalog_id = payload.get("catalog_id")
    if catalog_id is not None:
        _validate_identity(str(catalog_id), field_name="catalog id")
    package_id = payload.get("package_id")
    if package_id is not None:
        _validate_identity(str(package_id), field_name="package id")
    workspace = payload.get("workspace")
    if workspace is not None and (
        not isinstance(workspace, str) or not workspace.strip()
    ):
        raise ValueError("workspace must be a non-empty path")
    configuration = payload.get("configuration", {})
    if not isinstance(configuration, Mapping):
        raise ValueError("configuration must be an object")
    if source is IntegrationFlowSource.RAW_DESCRIPTOR:
        configuration = mcp_authoring_configuration_from_dict(
            configuration,
            target_id=target_id,
        ).to_dict()
    else:
        _validate_configuration(configuration)
    manifest = payload.get("manifest")
    if manifest is not None:
        if not isinstance(manifest, Mapping):
            raise ValueError("manifest must be an object")
        manifest = integration_package_to_dict(integration_package_from_dict(manifest))
    return {
        "source": source.value,
        "catalog_id": str(catalog_id) if catalog_id is not None else None,
        "manifest": manifest,
        "target_id": target_id,
        "scope": scope.value,
        "workspace": str(Path(workspace).expanduser().resolve()) if workspace else None,
        "package_id": str(package_id) if package_id is not None else None,
        "configuration": _json_value(configuration),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
