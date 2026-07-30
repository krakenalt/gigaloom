# ruff: noqa: E402, F401, F403, F405
"""Grouped integration projections, persistence, and validation helpers."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403


def integration_group_record_to_dict(record: IntegrationGroupRecord) -> dict[str, Any]:
    """Return one content-free group lifecycle projection."""
    projection = {
        "id": record.id,
        "plan_id": record.plan_id,
        "status": record.status.value,
        "component": record.component,
        "source": record.source,
        "catalog_id": record.catalog_id,
        "package_id": record.package_id,
        "package_version": record.package_version,
        "manifest_sha256": record.manifest_sha256,
        "target_mode": record.target_mode,
        "target_ids": list(record.target_ids),
        "aggregate_risk": record.aggregate_risk,
        "approval_hash": record.approval_hash,
        "children": [
            {
                "target_id": item.target_id,
                "scope": item.scope,
                "flow_id": item.flow_id,
                "plan_id": item.plan_id,
                "status": item.status,
                "verification_status": item.verification_status,
                "rollback_status": item.rollback_status,
                "error_code": item.error_code,
            }
            for item in record.children
        ],
        "repair_actions": list(record.repair_actions),
        "error_code": record.error_code,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "rollback_available": record.status is IntegrationGroupStatus.VERIFIED,
        "content_free": True,
    }
    if record.component == "extension_pack":
        projection["catalog_ids"] = {
            "skill": record.request["skill_catalog_id"],
            "mcp": record.request["mcp_catalog_id"],
        }
    return projection


def _public_plan(
    record: IntegrationGroupRecord,
    previews: list[dict[str, Any]],
    *,
    compatibility: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    permissions = {
        "network": any(item["plan"]["permissions"]["network"] for item in previews),
        "native_consent": any(
            item["plan"]["permissions"]["native_consent"] for item in previews
        ),
        "user_home": any(item["plan"]["permissions"]["user_home"] for item in previews),
    }
    plan = {
        "plan_id": record.plan_id,
        "package": {
            "id": record.package_id,
            "version": record.package_version,
            "manifest_sha256": record.manifest_sha256,
        },
        "component": record.component,
        "target_mode": "all_supported",
        "target_ids": list(record.target_ids),
        "aggregate_risk": record.aggregate_risk,
        "permissions": permissions,
        "children": [
            {
                "target_id": item["plan"]["target"]["id"],
                "scope": item["plan"]["target"]["scope"],
                "plan_id": item["plan"]["plan_id"],
                "configuration_diff": item["plan"]["configuration"]["diff"],
                "restart_required": item["plan"]["configuration"]["restart_required"],
                "verification_steps": item["plan"]["verification_steps"],
                "rollback_steps": item["plan"]["rollback_steps"],
            }
            for item in previews
        ],
        "atomicity": "recoverable_compensating_transaction",
        "approval_required": True,
        "content_free": True,
    }
    if compatibility is not None:
        plan["compatibility"] = compatibility
        plan["catalog_ids"] = {
            "skill": record.request["skill_catalog_id"],
            "mcp": record.request["mcp_catalog_id"],
        }
    return plan


def _normalize_request(
    payload: Mapping[str, Any], *, persisted: bool = False
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("integration group request must be an object")
    common = {
        "source",
        "scope",
        "workspace",
        "target_mode",
    }
    is_pack = payload.get("component") == "extension_pack" or any(
        key in payload for key in ("pack_id", "skill_catalog_id", "mcp_catalog_id")
    )
    if is_pack:
        allowed = common | {
            "component",
            "pack_id",
            "pack_version",
            "skill_catalog_id",
            "mcp_catalog_id",
            "mcp_configuration",
        }
        if persisted:
            allowed.add("compatibility")
    else:
        allowed = common | {"catalog_id", "configuration", "component"}
    if set(payload) - allowed:
        raise ValueError("integration group request contains unknown fields")
    if payload.get("source", "catalog") != "catalog":
        raise ValueError("all-target groups require a reviewed catalog source")
    if payload.get("target_mode", "all_supported") != "all_supported":
        raise ValueError("integration group target_mode must be all_supported")
    scope = InstallationScope(str(payload.get("scope") or "managed_home"))
    if scope is InstallationScope.USER_HOME:
        raise ValueError("all-target user-home expansion is not implicit")
    workspace = payload.get("workspace")
    if scope is InstallationScope.PROJECT:
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("project group requires an explicit workspace")
        workspace = str(Path(workspace).expanduser().resolve())
    elif workspace is not None:
        raise ValueError("managed-home group cannot include a workspace")
    if is_pack:
        pack_id = str(payload.get("pack_id") or "")
        pack_version = str(payload.get("pack_version") or "")
        skill_catalog_id = str(payload.get("skill_catalog_id") or "")
        mcp_catalog_id = str(payload.get("mcp_catalog_id") or "")
        if not _PACK_ID_RE.fullmatch(pack_id):
            raise ValueError("extension pack id is invalid")
        if not _PACK_VERSION_RE.fullmatch(pack_version):
            raise ValueError("extension pack version must be exact semver")
        if not skill_catalog_id or len(skill_catalog_id) > 256:
            raise ValueError("extension pack Skill catalog id is invalid")
        if not mcp_catalog_id or len(mcp_catalog_id) > 256:
            raise ValueError("extension pack MCP catalog id is invalid")
        mcp_configuration = payload.get("mcp_configuration", {})
        if not isinstance(mcp_configuration, Mapping):
            raise ValueError("extension pack MCP configuration must be an object")
        normalized = {
            "source": "catalog",
            "component": "extension_pack",
            "pack_id": pack_id,
            "pack_version": pack_version,
            "skill_catalog_id": skill_catalog_id,
            "mcp_catalog_id": mcp_catalog_id,
            "scope": scope.value,
            "workspace": workspace,
            "mcp_configuration": _json_value(mcp_configuration),
            "target_mode": "all_supported",
        }
        if persisted:
            compatibility = payload.get("compatibility")
            if not isinstance(compatibility, list):
                raise ValueError("extension pack compatibility state is invalid")
            normalized["compatibility"] = _json_value(compatibility)
        return normalized

    catalog_id = str(payload.get("catalog_id") or "")
    if not catalog_id or len(catalog_id) > 256:
        raise ValueError("integration group catalog_id is invalid")
    configuration = payload.get("configuration", {})
    if not isinstance(configuration, Mapping):
        raise ValueError("integration group configuration must be an object")
    return {
        "source": "catalog",
        "component": "single",
        "catalog_id": catalog_id,
        "scope": scope.value,
        "workspace": workspace,
        "configuration": _json_value(configuration),
        "target_mode": "all_supported",
    }


def _aggregate_risk(previews: list[dict[str, Any]]) -> str:
    decisions = {item["plan"]["risk"]["decision"] for item in previews}
    for decision in ("blocked", "provider_handoff", "review_required", "reviewed"):
        if decision in decisions:
            return decision
    return sorted(decisions)[0] if decisions else "review_required"


def _compatibility_item(
    status: str, target_id: str | None, reason_code: str | None
) -> dict[str, Any]:
    return {
        "status": status,
        "target_id": target_id,
        "reason_code": reason_code,
        "content_free": True,
    }


def _compatibility_failure_status(exc: Exception) -> str:
    return "unknown" if isinstance(exc, IntegrationFlowError) else "unsupported"


def _compatibility_failure_reason(exc: Exception) -> str:
    if isinstance(exc, IntegrationFlowError):
        return "target_probe_failed"
    return "incompatible_or_unavailable"


def _target_compatibility_status(items: list[Mapping[str, Any]]) -> str:
    statuses = {str(item["status"]) for item in items}
    if statuses == {"supported"}:
        return "supported"
    if "unknown" in statuses:
        return "unknown"
    return "unsupported"


def _record_plan_id(record: IntegrationGroupRecord) -> str:
    semantic = {
        "schema_version": INTEGRATION_GROUP_SCHEMA_VERSION,
        "source": record.source,
        "catalog_id": record.catalog_id,
        "package_id": record.package_id,
        "package_version": record.package_version,
        "manifest_sha256": record.manifest_sha256,
        "component": record.component,
        "target_mode": record.target_mode,
        "target_ids": list(record.target_ids),
        "scope": record.request["scope"],
        "workspace": record.request.get("workspace"),
        "children": [
            {
                "target_id": item.target_id,
                "flow_id": item.flow_id,
                "plan_id": item.plan_id,
            }
            for item in record.children
        ],
    }
    if record.component == "extension_pack":
        semantic["compatibility"] = record.request["compatibility"]
    return f"plan_{_json_hash(semantic)}"


def _private_record(record: IntegrationGroupRecord) -> dict[str, Any]:
    return {
        **integration_group_record_to_dict(record),
        "request": record.request,
        "children": [item.__dict__ for item in record.children],
    }


def _record_from_dict(value: Any) -> IntegrationGroupRecord:
    if not isinstance(value, Mapping):
        raise IntegrationGroupError("integration group record is invalid")
    try:
        record = IntegrationGroupRecord(
            id=str(value["id"]),
            plan_id=str(value["plan_id"]),
            status=IntegrationGroupStatus(str(value["status"])),
            component=str(value["component"]),
            source=str(value["source"]),
            catalog_id=str(value["catalog_id"]),
            package_id=str(value["package_id"]),
            package_version=str(value["package_version"]),
            manifest_sha256=str(value["manifest_sha256"]),
            target_mode=str(value["target_mode"]),
            target_ids=tuple(str(item) for item in value["target_ids"]),
            request=_normalize_request(value["request"], persisted=True),
            children=tuple(
                IntegrationGroupChild(
                    target_id=str(item["target_id"]),
                    scope=str(item["scope"]),
                    flow_id=str(item["flow_id"]),
                    plan_id=str(item["plan_id"]),
                    status=str(item["status"]),
                    receipt_id=(
                        str(item["receipt_id"]) if item.get("receipt_id") else None
                    ),
                    verification_status=str(item["verification_status"]),
                    rollback_status=str(item["rollback_status"]),
                    error_code=(
                        str(item["error_code"]) if item.get("error_code") else None
                    ),
                )
                for item in value["children"]
            ),
            aggregate_risk=str(value["aggregate_risk"]),
            approval_hash=(
                str(value["approval_hash"]) if value.get("approval_hash") else None
            ),
            repair_actions=tuple(str(item) for item in value["repair_actions"]),
            error_code=(str(value["error_code"]) if value.get("error_code") else None),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrationGroupError("integration group record is invalid") from exc
    _validate_group_id(record.id)
    _validate_plan_id(record.plan_id)
    return record


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("integration group payload is too deep")
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError("integration group text is too long")
        return value
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise ValueError("integration group object is too large")
        return {
            str(key): _json_value(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 256:
            raise ValueError("integration group list is too large")
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ValueError("integration group payload must contain JSON values only")


def _error_code(exc: Exception | None) -> str | None:
    return type(exc).__name__ if exc is not None else None


def _validate_group_id(value: str) -> None:
    if not _GROUP_ID_RE.fullmatch(value):
        raise ValueError("integration group id is invalid")


def _validate_plan_id(value: str) -> None:
    if not _PLAN_ID_RE.fullmatch(value):
        raise ValueError("integration group plan id is invalid")


def _validate_authority(value: str) -> None:
    if not _AUTHORITY_RE.fullmatch(value):
        raise ValueError("integration group approval authority is invalid")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [name for name in globals() if not name.startswith("__")]
