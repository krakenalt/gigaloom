# ruff: noqa: E402, F401, F403, F405
"""Internal integration flow implementation slice."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .models import *  # noqa: F403
from .projection import *  # noqa: F403
from .resolution import *  # noqa: F403
from .state import *  # noqa: F403


class _FlowOrchestrationMixin:
    """Implementation slice for application-owned integration flows."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        skill_capability_provider: Callable[[str], SkillCapabilitySnapshot]
        | None = None,
        mcp_driver_provider: Callable[[str], Any] | None = None,
        plugin_driver_provider: Callable[[str, Path, InstallationScope], Any]
        | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "integrations"
        self.path = self.root / "flows.json"
        self.lock_path = self.root / ".flows.json.lock"
        self.catalog = IntegrationCatalogStore(self.data_dir)
        self._external_skill_store = ExternalSkillStore(
            self.data_dir / "integrations" / "external-skills"
        )
        self._skill_capability_provider = (
            skill_capability_provider or probe_skill_target
        )
        self._mcp_driver_provider = mcp_driver_provider or self._default_mcp_driver
        self._plugin_driver_provider = plugin_driver_provider
        self._managed_mcp_inventory = ManagedMCPInventoryStore(self.data_dir)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def inventory(self) -> dict[str, Any]:
        """Return bounded sources, targets, catalog entries, and recent flows."""
        self._ensure_catalog_seeded()
        snapshot = self.catalog.snapshot()
        entries = tuple(snapshot.entries)
        now = self._now().astimezone(timezone.utc)
        return {
            "schema_version": INTEGRATION_FLOW_SCHEMA_VERSION,
            "sources": [
                {
                    "id": item.value,
                    "network_required": item
                    in {IntegrationFlowSource.MARKETPLACE, IntegrationFlowSource.GIT},
                    "immutable_input_required": item
                    not in {
                        IntegrationFlowSource.CATALOG,
                        IntegrationFlowSource.RAW_DESCRIPTOR,
                    },
                }
                for item in IntegrationFlowSource
            ],
            "catalog_sources": [
                _catalog_source_to_dict(item, now=now) for item in snapshot.sources
            ],
            "targets": [
                {
                    **extension_target_descriptor_to_dict(item),
                    "execution_owner": _execution_owner(item.id),
                }
                for item in BUILTIN_FLOW_TARGETS
            ],
            "catalog": [_catalog_entry_to_dict(item) for item in entries[:200]],
            "flows": [integration_flow_record_to_dict(item) for item in self.list()],
            "content_free": True,
        }

    def list(self) -> tuple[IntegrationFlowRecord, ...]:
        """Return recent flows in reverse update order."""
        records = self._read_records()
        return tuple(
            sorted(records.values(), key=lambda item: item.updated_at, reverse=True)
        )[:MAX_INTEGRATION_FLOWS]

    def get(self, flow_id: str) -> IntegrationFlowRecord:
        """Return one exact durable flow."""
        _validate_flow_id(flow_id)
        record = self._read_records().get(flow_id)
        if record is None:
            raise IntegrationFlowNotFoundError(flow_id)
        return record

    def preview(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve one immutable package and persist an exact approval preview."""
        request = _normalize_request(payload)
        resolved = self._resolve_preview(request)
        plan = _public_plan(request, resolved)
        timestamp = self._timestamp()
        flow_id = f"flow_{uuid4().hex}"
        record = IntegrationFlowRecord(
            id=flow_id,
            plan_id=plan["plan_id"],
            status=IntegrationFlowStatus.AWAITING_APPROVAL,
            source=IntegrationFlowSource(request["source"]),
            package_id=resolved.package.id,
            package_version=resolved.package.version,
            manifest_sha256=integration_package_semantic_hash(resolved.package),
            source_provenance=resolved.source_provenance,
            target_id=resolved.target.id,
            scope=InstallationScope(request["scope"]),
            workspace=request.get("workspace"),
            request=request,
            receipt_id=None,
            verification_status="not_started",
            rollback_available=False,
            error_code=None,
            created_at=timestamp,
            updated_at=timestamp,
            events=(
                IntegrationFlowEvent(
                    stage="preview",
                    status="completed",
                    occurred_at=timestamp,
                ),
            ),
        )
        self._put(record)
        return {"flow": integration_flow_record_to_dict(record), "plan": plan}

    def probe(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve one immutable package without persisting an approval flow."""
        request = _normalize_request(payload)
        resolved = self._resolve_preview(request)
        return {"plan": _public_plan(request, resolved)}

    def apply(
        self,
        flow_id: str,
        *,
        plan_id: str,
        authority: str,
        allow_network: bool = False,
        allow_user_home: bool = False,
        native_consent_acknowledged: bool = False,
    ) -> dict[str, Any]:
        """Apply one exact preview or return its explicit target-owned handoff."""
        record = self.get(flow_id)
        _validate_plan_id(plan_id)
        _validate_identity(authority, field_name="approval authority")
        if record.plan_id != plan_id:
            raise IntegrationFlowConflictError("approval does not match the preview")
        if record.status is not IntegrationFlowStatus.AWAITING_APPROVAL:
            if record.status in {
                IntegrationFlowStatus.VERIFIED,
                IntegrationFlowStatus.HANDOFF_REQUIRED,
            }:
                return {"flow": integration_flow_record_to_dict(record)}
            raise IntegrationFlowConflictError("flow cannot be applied in its state")
        resolved = self._resolve_preview(record.request)
        current_plan = _public_plan(record.request, resolved)
        if current_plan["plan_id"] != plan_id:
            raise IntegrationFlowConflictError("integration preview is stale")
        if current_plan["permissions"]["network"] and not allow_network:
            raise IntegrationFlowConflictError("network access requires approval")
        if record.scope is InstallationScope.USER_HOME and not allow_user_home:
            raise IntegrationFlowConflictError("user-home access requires approval")
        if (
            current_plan["permissions"]["native_consent"]
            and not native_consent_acknowledged
        ):
            raise IntegrationFlowConflictError(
                "native consent requires acknowledgement"
            )
        applying = self._transition(record, IntegrationFlowStatus.APPLYING, "apply")
        try:
            if not resolved.executable:
                completed = self._transition(
                    applying,
                    IntegrationFlowStatus.HANDOFF_REQUIRED,
                    "handoff",
                    verification_status="provider_owned",
                )
                return {
                    "flow": integration_flow_record_to_dict(completed),
                    "handoff": {
                        "owner": resolved.execution_owner,
                        "reason": resolved.handoff_reason,
                        "mutation_performed": False,
                    },
                }
            if resolved.target.id in _SKILL_TARGETS:
                receipt_id, verification_status = self._apply_skill(
                    record.request,
                    resolved,
                    authority=authority,
                    allow_user_home=allow_user_home,
                )
            elif resolved.target.id in _MCP_TARGET_IDS:
                receipt_id, verification_status = self._apply_mcp(
                    record.request,
                    resolved,
                    authority=authority,
                    allow_user_home=allow_user_home,
                )
            else:
                receipt_id, verification_status = self._apply_plugin(
                    record.request,
                    resolved,
                    authority=authority,
                    allow_network=allow_network,
                    allow_user_home=allow_user_home,
                    native_consent_acknowledged=native_consent_acknowledged,
                )
            verified = self._transition(
                applying,
                IntegrationFlowStatus.VERIFIED,
                "verify",
                receipt_id=receipt_id,
                verification_status=verification_status,
                rollback_available=True,
            )
            return {"flow": integration_flow_record_to_dict(verified)}
        except Exception as exc:
            self._transition(
                applying,
                IntegrationFlowStatus.FAILED,
                "failure",
                error_code=type(exc).__name__,
                verification_status="failed",
            )
            raise IntegrationFlowError(
                "integration apply failed; details were omitted"
            ) from exc

    def rollback(self, flow_id: str) -> dict[str, Any]:
        """Roll back one verified application-owned transaction."""
        record = self.get(flow_id)
        if (
            record.status is not IntegrationFlowStatus.VERIFIED
            or not record.rollback_available
            or record.receipt_id is None
        ):
            raise IntegrationFlowConflictError("flow has no reversible transaction")
        try:
            resolved = self._resolve_preview(record.request, existing=True)
            if resolved.target.id in _MCP_TARGET_IDS:
                self._rollback_mcp(record, resolved)
            elif resolved.target.id in _SKILL_TARGETS:
                installer = self._skill_installer(record.request, resolved.root)
                installer.rollback(record.receipt_id)
            elif resolved.target.id in _PLUGIN_TARGET_IDS:
                scope = InstallationScope(record.request["scope"])
                native_request = self._plugin_request(
                    record.request,
                    resolved.package,
                    resolved.target.id,
                    resolved.root,
                    scope,
                )
                self._plugin_driver(resolved.target.id, resolved.root, scope).rollback(
                    native_request
                )
            else:
                raise IntegrationFlowConflictError(
                    "rollback remains owned by the selected native target"
                )
            updated = self._transition(
                record,
                IntegrationFlowStatus.ROLLED_BACK,
                "rollback",
                verification_status="rolled_back",
                rollback_available=False,
            )
            return {"flow": integration_flow_record_to_dict(updated)}
        except IntegrationFlowConflictError:
            raise
        except Exception as exc:
            self._transition(
                record,
                IntegrationFlowStatus.FAILED,
                "rollback_failure",
                error_code=type(exc).__name__,
                verification_status="rollback_failed",
            )
            raise IntegrationFlowError(
                "integration rollback failed; details were omitted"
            ) from exc

    def uninstall_owned(
        self,
        flow_id: str,
        *,
        receipt_id: str,
        authority: str,
        allow_user_home: bool = False,
    ) -> dict[str, Any]:
        """Remove only material owned by one exact verified installation."""
        record = self.get(flow_id)
        _validate_identity(authority, field_name="approval authority")
        if (
            record.status is not IntegrationFlowStatus.VERIFIED
            or record.receipt_id is None
            or record.receipt_id != receipt_id
        ):
            raise IntegrationFlowConflictError(
                "uninstall requires the exact verified installation receipt"
            )
        target = _target(record.target_id)
        root = self._target_root(record.request, target, create=False)
        package = self._resolve_package(
            record.request,
            record.source,
            target,
            record.scope,
        )
        try:
            if target.id in _SKILL_TARGETS:
                result = self._skill_installer(record.request, root).rollback(
                    receipt_id
                )
                outcome = result.status
            elif target.id == HARNESS_MANAGED_MCP_TARGET_ID:
                result = self._managed_mcp_inventory.rollback(receipt_id)
                outcome = result.status
            elif target.id in _MCP_TARGET_IDS:
                driver = self._mcp_driver_provider(target.id)
                plan = driver.preview_uninstall(receipt_id)
                result = driver.uninstall(
                    plan,
                    InstallationApproval(
                        plan_id=plan.plan_id,
                        authority=authority,
                        allow_user_home=allow_user_home,
                    ),
                )
                outcome = result.status
            elif target.id in _PLUGIN_TARGET_IDS:
                driver = self._plugin_driver(target.id, root, record.scope)
                native_request = self._plugin_request(
                    record.request,
                    package,
                    target.id,
                    root,
                    record.scope,
                )
                plan = driver.preview_uninstall(native_request)
                if target.id == CODEX_PLUGIN_TARGET_ID:
                    approval = CodexPluginApproval(
                        plan_id=plan.plan_id,
                        authority=authority,
                        native_consent_acknowledged=True,
                        allow_user_home=allow_user_home,
                    )
                elif target.id == CLAUDE_PLUGIN_TARGET_ID:
                    approval = ClaudePluginApproval(
                        plan_id=plan.plan_id,
                        authority=authority,
                        native_consent_acknowledged=True,
                        allow_user_home=allow_user_home,
                    )
                else:
                    approval = GeminiExtensionApproval(
                        plan_id=plan.plan_id,
                        authority=authority,
                        native_consent_acknowledged=True,
                        source_trust_acknowledged=True,
                        allow_user_home=allow_user_home,
                    )
                result = driver.uninstall(native_request, plan, approval)
                outcome = result.status
            else:
                raise IntegrationFlowConflictError(
                    "selected target has no application-owned uninstall surface"
                )
            return {
                "flow_id": record.id,
                "receipt_id": receipt_id,
                "status": str(outcome),
                "installer_owned_only": True,
                "content_free": True,
            }
        except IntegrationFlowConflictError:
            raise
        except Exception as exc:
            raise IntegrationFlowError(
                "integration uninstall failed; details were omitted"
            ) from exc
