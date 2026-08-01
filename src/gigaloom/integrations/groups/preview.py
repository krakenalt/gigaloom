# ruff: noqa: E402, F401, F403, F405
"""Internal grouped-integration preview."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .helpers import *  # noqa: F403
from .models import *  # noqa: F403


class _GroupPreviewMixin:
    """Internal grouped-integration transaction operations."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        flow_service: IntegrationFlowService | None = None,
        now: Any | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "integrations"
        self.path = self.root / "groups.json"
        self.lock_path = self.root / ".groups.json.lock"
        self.flows = flow_service or IntegrationFlowService(self.data_dir)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list(self) -> tuple[IntegrationGroupRecord, ...]:
        """Return recent group operations in reverse update order."""
        return tuple(
            sorted(
                self._read().values(), key=lambda item: item.updated_at, reverse=True
            )[:MAX_INTEGRATION_GROUPS]
        )

    def get(self, group_id: str) -> IntegrationGroupRecord:
        """Return one exact durable group record."""
        _validate_group_id(group_id)
        record = self._read().get(group_id)
        if record is None:
            raise IntegrationGroupNotFoundError(group_id)
        return record

    def preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Preview every explicit supported target before any target mutation."""
        request = _normalize_request(payload)
        self.flows.inventory()
        if request["component"] == "extension_pack":
            return self._preview_extension_pack(request)
        entry = self.flows.catalog.get(request["catalog_id"])
        if entry is None:
            raise ValueError("group catalog selection was not found")
        if entry.package is not None:
            component_types = {item.type for item in entry.package.components}
            if component_types == {IntegrationComponentType.SKILL}:
                component = "skill"
                supported = {item.target_id for item in entry.package.compatibility}
                target_ids = tuple(item for item in _SKILL_TARGETS if item in supported)
                package_id = entry.package.id
                package_version = entry.package.version
                manifest_hash = integration_package_semantic_hash(entry.package)
            else:
                raise ValueError("all-target groups support only Skills or MCP")
        elif entry.mcp_response is not None:
            component = "mcp"
            target_ids = _MCP_TARGETS
            package_id = entry.package_id
            package_version = entry.version
            manifest_hash = entry.content_hash
        else:
            raise ValueError("catalog entry is discovery-only and cannot be installed")
        if not target_ids:
            raise ValueError("catalog package has no supported all-target expansion")

        child_payloads = [
            {
                "source": "catalog",
                "catalog_id": request["catalog_id"],
                "target_id": target_id,
                "scope": request["scope"],
                "workspace": request.get("workspace"),
                "configuration": request["configuration"],
            }
            for target_id in target_ids
        ]
        previews: list[dict[str, Any]] = []
        try:
            for child_payload in child_payloads:
                preview = self.flows.preview(child_payload)
                if not preview["plan"]["target"]["executable"]:
                    raise ValueError("all-target child requires an executable owner")
                previews.append(preview)
        except Exception as exc:
            raise ValueError("all child previews must succeed before approval") from exc

        semantic = {
            "schema_version": INTEGRATION_GROUP_SCHEMA_VERSION,
            "source": "catalog",
            "catalog_id": request["catalog_id"],
            "package_id": package_id,
            "package_version": package_version,
            "manifest_sha256": manifest_hash,
            "component": component,
            "target_mode": "all_supported",
            "target_ids": list(target_ids),
            "scope": request["scope"],
            "workspace": request.get("workspace"),
            "children": [
                {
                    "target_id": item["plan"]["target"]["id"],
                    "flow_id": item["flow"]["id"],
                    "plan_id": item["plan"]["plan_id"],
                }
                for item in previews
            ],
        }
        plan_id = f"plan_{_json_hash(semantic)}"
        aggregate_risk = _aggregate_risk(previews)
        timestamp = self._timestamp()
        record = IntegrationGroupRecord(
            id=f"group_{uuid4().hex}",
            plan_id=plan_id,
            status=IntegrationGroupStatus.AWAITING_APPROVAL,
            component=component,
            source="catalog",
            catalog_id=request["catalog_id"],
            package_id=package_id,
            package_version=package_version,
            manifest_sha256=manifest_hash,
            target_mode="all_supported",
            target_ids=target_ids,
            request=request,
            children=tuple(
                IntegrationGroupChild(
                    target_id=item["plan"]["target"]["id"],
                    scope=item["plan"]["target"]["scope"],
                    flow_id=item["flow"]["id"],
                    plan_id=item["plan"]["plan_id"],
                    status=item["flow"]["status"],
                )
                for item in previews
            ),
            aggregate_risk=aggregate_risk,
            approval_hash=None,
            repair_actions=(),
            error_code=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._put(record)
        return {
            "group": integration_group_record_to_dict(record),
            "plan": _public_plan(record, previews),
        }

    def _preview_extension_pack(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Compile one reviewed Skill and MCP pin into compatible target children."""
        skill_entry = self.flows.catalog.get(request["skill_catalog_id"])
        mcp_entry = self.flows.catalog.get(request["mcp_catalog_id"])
        if skill_entry is None or skill_entry.package is None:
            raise ValueError("extension pack Skill selection was not found")
        if {item.type for item in skill_entry.package.components} != {
            IntegrationComponentType.SKILL
        }:
            raise ValueError("extension pack Skill selection is not portable")
        if mcp_entry is None or mcp_entry.mcp_response is None:
            raise ValueError("extension pack MCP selection was not found")

        declared_skill_targets = {
            item.target_id for item in skill_entry.package.compatibility
        }
        previews: list[dict[str, Any]] = []
        compatibility: list[dict[str, Any]] = []
        for target, skill_target, mcp_target in _PACK_TARGETS:
            components: dict[str, dict[str, Any]] = {}
            target_previews: list[dict[str, Any]] = []
            if skill_target is None:
                components["skill"] = _compatibility_item(
                    "not_applicable", None, "target_has_no_skill_surface"
                )
            elif skill_target not in declared_skill_targets:
                components["skill"] = _compatibility_item(
                    "unsupported", skill_target, "package_target_not_declared"
                )
            else:
                skill_preview, skill_compatibility = self._preview_pack_child(
                    request,
                    catalog_id=request["skill_catalog_id"],
                    target_id=skill_target,
                    configuration={},
                )
                components["skill"] = skill_compatibility
                if skill_preview is not None:
                    target_previews.append(skill_preview)

            mcp_preview, mcp_compatibility = self._preview_pack_child(
                request,
                catalog_id=request["mcp_catalog_id"],
                target_id=mcp_target,
                configuration=request["mcp_configuration"],
            )
            components["mcp"] = mcp_compatibility
            if mcp_preview is not None:
                target_previews.append(mcp_preview)

            applicable = [
                item
                for item in components.values()
                if item["status"] != "not_applicable"
            ]
            included = bool(applicable) and all(
                item["status"] == "supported" for item in applicable
            )
            status = _target_compatibility_status(applicable)
            compatibility.append(
                {
                    "target": target,
                    "status": status,
                    "included": included,
                    "components": components,
                }
            )
            if included:
                previews.extend(
                    self.flows.preview(item["request"]) for item in target_previews
                )

        if not any(
            item["included"] and item["target"] != "harness" for item in compatibility
        ):
            raise ValueError("extension pack has no compatible native agent target")

        pack_semantic = {
            "schema_version": INTEGRATION_GROUP_SCHEMA_VERSION,
            "pack_id": request["pack_id"],
            "pack_version": request["pack_version"],
            "skill_catalog_id": request["skill_catalog_id"],
            "skill_manifest_sha256": integration_package_semantic_hash(
                skill_entry.package
            ),
            "mcp_catalog_id": request["mcp_catalog_id"],
            "mcp_content_sha256": mcp_entry.content_hash,
            "mcp_configuration_sha256": _json_hash(request["mcp_configuration"]),
        }
        manifest_hash = _json_hash(pack_semantic)
        catalog_id = f"pack:{manifest_hash}"
        semantic = {
            "schema_version": INTEGRATION_GROUP_SCHEMA_VERSION,
            "source": "catalog",
            "catalog_id": catalog_id,
            "package_id": request["pack_id"],
            "package_version": request["pack_version"],
            "manifest_sha256": manifest_hash,
            "component": "extension_pack",
            "target_mode": "all_supported",
            "target_ids": [item["plan"]["target"]["id"] for item in previews],
            "scope": request["scope"],
            "workspace": request.get("workspace"),
            "compatibility": compatibility,
            "children": [
                {
                    "target_id": item["plan"]["target"]["id"],
                    "flow_id": item["flow"]["id"],
                    "plan_id": item["plan"]["plan_id"],
                }
                for item in previews
            ],
        }
        plan_id = f"plan_{_json_hash(semantic)}"
        timestamp = self._timestamp()
        stored_request = {**request, "compatibility": compatibility}
        record = IntegrationGroupRecord(
            id=f"group_{uuid4().hex}",
            plan_id=plan_id,
            status=IntegrationGroupStatus.AWAITING_APPROVAL,
            component="extension_pack",
            source="catalog",
            catalog_id=catalog_id,
            package_id=request["pack_id"],
            package_version=request["pack_version"],
            manifest_sha256=manifest_hash,
            target_mode="all_supported",
            target_ids=tuple(item["plan"]["target"]["id"] for item in previews),
            request=stored_request,
            children=tuple(
                IntegrationGroupChild(
                    target_id=item["plan"]["target"]["id"],
                    scope=item["plan"]["target"]["scope"],
                    flow_id=item["flow"]["id"],
                    plan_id=item["plan"]["plan_id"],
                    status=item["flow"]["status"],
                )
                for item in previews
            ),
            aggregate_risk=_aggregate_risk(previews),
            approval_hash=None,
            repair_actions=(),
            error_code=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._put(record)
        return {
            "group": integration_group_record_to_dict(record),
            "plan": _public_plan(record, previews, compatibility=compatibility),
        }

    def _preview_pack_child(
        self,
        request: Mapping[str, Any],
        *,
        catalog_id: str,
        target_id: str,
        configuration: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        payload = {
            "source": "catalog",
            "catalog_id": catalog_id,
            "target_id": target_id,
            "scope": request["scope"],
            "workspace": request.get("workspace"),
            "configuration": configuration,
        }
        try:
            probe = self.flows.probe(payload)
        except (IntegrationFlowError, ValueError) as exc:
            return None, _compatibility_item(
                _compatibility_failure_status(exc),
                target_id,
                _compatibility_failure_reason(exc),
            )
        if not probe["plan"]["target"]["executable"]:
            return None, _compatibility_item(
                "unsupported", target_id, "target_requires_provider_handoff"
            )
        return {"request": payload}, _compatibility_item("supported", target_id, None)
