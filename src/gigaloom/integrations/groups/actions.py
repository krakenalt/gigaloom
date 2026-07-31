# ruff: noqa: E402, F401, F403, F405
"""Internal grouped-integration actions."""

from __future__ import annotations

from .dependencies import *  # noqa: F403
from .helpers import *  # noqa: F403
from .models import *  # noqa: F403


class _GroupActionsMixin:
    """Internal grouped-integration transaction operations."""

    def apply(
        self,
        group_id: str,
        *,
        plan_id: str,
        authority: str,
        allow_network: bool = False,
        allow_user_home: bool = False,
        native_consent_acknowledged: bool = False,
    ) -> dict[str, Any]:
        """Apply ordered children and compensate safely after any failure."""
        record = self.get(group_id)
        _validate_plan_id(plan_id)
        _validate_authority(authority)
        if record.plan_id != plan_id:
            raise IntegrationGroupConflictError("approval does not match group preview")
        if _record_plan_id(record) != record.plan_id:
            raise IntegrationGroupConflictError("group target expansion is stale")
        if record.status in {
            IntegrationGroupStatus.VERIFIED,
            IntegrationGroupStatus.COMPENSATED,
            IntegrationGroupStatus.ROLLED_BACK,
        }:
            return {"group": integration_group_record_to_dict(record)}
        if record.status is not IntegrationGroupStatus.AWAITING_APPROVAL:
            raise IntegrationGroupConflictError("group requires recovery in its state")
        child_plans = [self._child_plan(item) for item in record.children]
        network = any(item["permissions"]["network"] for item in child_plans)
        native = any(item["permissions"]["native_consent"] for item in child_plans)
        user_home = any(item["permissions"]["user_home"] for item in child_plans)
        if network and not allow_network:
            raise IntegrationGroupConflictError(
                "group network access requires approval"
            )
        if native and not native_consent_acknowledged:
            raise IntegrationGroupConflictError(
                "group native consent requires acknowledgement"
            )
        if user_home and not allow_user_home:
            raise IntegrationGroupConflictError(
                "group user-home access requires approval"
            )
        approval_hash = _json_hash(
            {
                "plan_id": plan_id,
                "authority": authority,
                "allow_network": allow_network,
                "allow_user_home": allow_user_home,
                "native_consent_acknowledged": native_consent_acknowledged,
            }
        )
        record = self._transition(
            record,
            IntegrationGroupStatus.APPLYING,
            approval_hash=approval_hash,
        )
        try:
            for child in record.children:
                result = self.flows.apply(
                    child.flow_id,
                    plan_id=child.plan_id,
                    authority=authority,
                    allow_network=allow_network,
                    allow_user_home=allow_user_home,
                    native_consent_acknowledged=native_consent_acknowledged,
                )
                flow = result["flow"]
                if flow["status"] != IntegrationFlowStatus.VERIFIED.value:
                    raise IntegrationGroupError("group child did not verify")
                record = self._update_child(
                    record,
                    child.target_id,
                    status=flow["status"],
                    verification_status=flow["verification_status"],
                    receipt_id=flow.get("receipt_id"),
                )
            record = self._transition(record, IntegrationGroupStatus.VERIFIED)
            return {"group": integration_group_record_to_dict(record)}
        except Exception as exc:
            return self._compensate(record, cause=exc)

    def recover(self, group_id: str) -> dict[str, Any]:
        """Deterministically compensate interrupted or repair-required work."""
        record = self.get(group_id)
        if record.status in {
            IntegrationGroupStatus.COMPENSATED,
            IntegrationGroupStatus.ROLLED_BACK,
            IntegrationGroupStatus.VERIFIED,
        }:
            return {"group": integration_group_record_to_dict(record)}
        if record.status not in {
            IntegrationGroupStatus.APPLYING,
            IntegrationGroupStatus.COMPENSATING,
            IntegrationGroupStatus.REPAIR_REQUIRED,
            IntegrationGroupStatus.ROLLING_BACK,
        }:
            raise IntegrationGroupConflictError("group has no recoverable work")
        terminal_rollback = record.status is IntegrationGroupStatus.ROLLING_BACK or (
            record.status is IntegrationGroupStatus.REPAIR_REQUIRED
            and record.error_code == "rollback_failed"
        )
        return self._compensate(
            record,
            cause=None,
            terminal_rollback=terminal_rollback,
        )

    def rollback(self, group_id: str) -> dict[str, Any]:
        """Roll back every verified child in reverse deterministic order."""
        record = self.get(group_id)
        if record.status is IntegrationGroupStatus.ROLLED_BACK:
            return {"group": integration_group_record_to_dict(record)}
        if record.status is not IntegrationGroupStatus.VERIFIED:
            raise IntegrationGroupConflictError("group is not verified")
        record = self._transition(record, IntegrationGroupStatus.ROLLING_BACK)
        return self._compensate(record, cause=None, terminal_rollback=True)

    def _compensate(
        self,
        record: IntegrationGroupRecord,
        *,
        cause: Exception | None,
        terminal_rollback: bool = False,
    ) -> dict[str, Any]:
        state = (
            IntegrationGroupStatus.ROLLING_BACK
            if terminal_rollback
            else IntegrationGroupStatus.COMPENSATING
        )
        record = self._transition(record, state, error_code=_error_code(cause))
        repair_actions: list[str] = []
        for child in reversed(record.children):
            try:
                flow = self.flows.get(child.flow_id)
            except Exception:
                repair_actions.append(
                    f"inspect-child:{child.target_id}:{child.flow_id}"
                )
                continue
            if flow.status is IntegrationFlowStatus.ROLLED_BACK:
                record = self._update_child(
                    record, child.target_id, rollback_status="rolled_back"
                )
                continue
            if flow.status is not IntegrationFlowStatus.VERIFIED:
                continue
            try:
                result = self.flows.rollback(child.flow_id)["flow"]
                record = self._update_child(
                    record,
                    child.target_id,
                    status=result["status"],
                    rollback_status="rolled_back",
                )
            except Exception:
                repair_actions.append(
                    f"retry-safe-rollback:{child.target_id}:{child.flow_id}"
                )
                record = self._update_child(
                    record,
                    child.target_id,
                    rollback_status="repair_required",
                    error_code="rollback_failed",
                )
        if repair_actions:
            record = self._transition(
                record,
                IntegrationGroupStatus.REPAIR_REQUIRED,
                repair_actions=tuple(repair_actions),
                error_code=_error_code(cause) or "rollback_failed",
            )
        else:
            record = self._transition(
                record,
                (
                    IntegrationGroupStatus.ROLLED_BACK
                    if terminal_rollback
                    else IntegrationGroupStatus.COMPENSATED
                ),
                repair_actions=(),
                error_code=_error_code(cause),
            )
        return {"group": integration_group_record_to_dict(record)}

    def _child_plan(self, child: IntegrationGroupChild) -> dict[str, Any]:
        flow = self.flows.get(child.flow_id)
        resolved = self.flows._resolve_preview(flow.request, existing=True)
        plan = _child_public_plan(flow.request, resolved)
        if plan["plan_id"] != child.plan_id:
            raise IntegrationGroupConflictError("group child preview is stale")
        return plan

    def _update_child(
        self,
        record: IntegrationGroupRecord,
        target_id: str,
        **changes: Any,
    ) -> IntegrationGroupRecord:
        children = tuple(
            replace(item, **changes) if item.target_id == target_id else item
            for item in record.children
        )
        updated = replace(record, children=children, updated_at=self._timestamp())
        self._put(updated)
        return updated
