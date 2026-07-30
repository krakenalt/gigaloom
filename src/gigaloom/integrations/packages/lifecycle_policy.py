# ruff: noqa: E402, F401, F403, F405
"""Durable product lifecycle for installed integrations and extension packs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from uuid import uuid4

from gigaloom.integration_catalog import (
    CatalogConflictError,
    CatalogSourceType,
)
from gigaloom.integration_flows import (
    BUILTIN_FLOW_TARGETS,
    IntegrationFlowRecord,
    IntegrationFlowService,
    IntegrationFlowStatus,
)
from gigaloom.integration_groups import (
    GroupedIntegrationService,
    IntegrationGroupStatus,
)
from gigaloom.integration_packages import IntegrationComponentType
from gigaloom.integration_runtime import IntegrationRuntimeStore
from gigaloom.diagnostics.inventory.capabilities import IntegrationLifecycle
from gigaloom.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_LIFECYCLE_SCHEMA_VERSION = 1
MAX_LIFECYCLE_OPERATIONS = 500
_OPERATION_ID_RE = re.compile(r"iop_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
from .lifecycle_models import *  # noqa: F403
from .lifecycle_support import *  # noqa: F403


class _LifecyclePolicyMixin:
    """Implementation slice for integration lifecycle operations."""

    def _preview(
        self,
        *,
        kind: str,
        target_id: str,
        flows: Sequence[IntegrationFlowRecord],
        action: IntegrationLifecycleAction,
    ) -> dict[str, Any]:
        if not flows:
            raise IntegrationLifecycleConflictError(
                "lifecycle operation requires an installed target"
            )
        with exclusive_file_lock(self.lock_path):
            stored = self._read_unlocked()
            states = [
                self._state_projection(flow, stored["states"].get(flow.id))
                for flow in flows
            ]
            for flow, state in zip(flows, states, strict=True):
                self._validate_transition(flow, state, action)
            active = {flow.id: self._active_sessions(flow) for flow in flows}
            if action is IntegrationLifecycleAction.UNINSTALL and any(active.values()):
                raise IntegrationLifecycleConflictError(
                    "active sessions retain this revision; uninstall is blocked"
                )
            catalog_revision: int | None = None
            if action is IntegrationLifecycleAction.DELETE_DEFINITION:
                catalog_revision = self._validate_definition_deletion(flows[0], stored)
            expected_revisions = {
                state["flow_id"]: state["revision"] for state in states
            }
            confirmation_id = (
                self.groups.get(target_id).package_id
                if kind == "group"
                else flows[0].package_id
            )
            semantic = {
                "schema_version": INTEGRATION_LIFECYCLE_SCHEMA_VERSION,
                "kind": kind,
                "target_id": target_id,
                "action": action.value,
                "flow_ids": [flow.id for flow in flows],
                "expected_revisions": expected_revisions,
                "receipts": [state["receipt_id"] for state in states],
                "catalog_revision": catalog_revision,
            }
            operation_id = f"iop_{uuid4().hex}"
            timestamp = self._timestamp()
            operation = {
                "id": operation_id,
                "plan_id": f"plan_{_json_hash(semantic)}",
                "kind": kind,
                "target_id": target_id,
                "action": action.value,
                "flow_ids": [flow.id for flow in flows],
                "expected_revisions": expected_revisions,
                "catalog_revision": catalog_revision,
                "status": IntegrationLifecycleOperationStatus.AWAITING_APPROVAL.value,
                "confirmation_required": action
                in {
                    IntegrationLifecycleAction.UNINSTALL,
                    IntegrationLifecycleAction.DELETE_DEFINITION,
                },
                "confirmation_id": confirmation_id,
                "active_sessions": {
                    flow_id: [item["session_id"] for item in items]
                    for flow_id, items in active.items()
                },
                "completed_flow_ids": [],
                "authority_sha256": None,
                "receipt_id": None,
                "recovery_actions": [],
                "error_code": None,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            stored["operations"][operation_id] = operation
            if len(stored["operations"]) > MAX_LIFECYCLE_OPERATIONS:
                oldest = sorted(
                    stored["operations"].values(),
                    key=lambda value: str(value["updated_at"]),
                )
                stored["operations"] = {
                    item["id"]: item for item in oldest[-MAX_LIFECYCLE_OPERATIONS:]
                }
            self._write_unlocked(stored)
        return {
            "operation": self._public_operation(operation),
            "plan": {
                "plan_id": operation["plan_id"],
                "kind": kind,
                "target_id": target_id,
                "action": action.value,
                "expected_revisions": expected_revisions,
                "confirmation_required": operation["confirmation_required"],
                "confirmation_id": confirmation_id,
                "active_session_count": sum(len(items) for items in active.values()),
                "active_sessions_retain_revision": action
                is IntegrationLifecycleAction.DISABLE,
                "effects": [
                    self._effect_projection(flow, action, active[flow.id])
                    for flow in flows
                ],
                "approval_required": True,
                "content_free": True,
            },
        }

    def _validate_transition(
        self,
        flow: IntegrationFlowRecord,
        state: Mapping[str, Any],
        action: IntegrationLifecycleAction,
    ) -> None:
        current = state["state"]
        allowed = {
            IntegrationLifecycleAction.ENABLE: {
                IntegrationLifecycle.DISABLED.value,
            },
            IntegrationLifecycleAction.DISABLE: {
                IntegrationLifecycle.ENABLED.value,
            },
            IntegrationLifecycleAction.UNINSTALL: {
                IntegrationLifecycle.ENABLED.value,
                IntegrationLifecycle.DISABLED.value,
            },
            IntegrationLifecycleAction.DELETE_DEFINITION: {
                IntegrationLifecycle.DEFINITION_ONLY.value,
                IntegrationLifecycle.UNINSTALLED.value,
            },
        }[action]
        if current not in allowed:
            raise IntegrationLifecycleConflictError(
                f"{action.value} is unavailable while integration is {current}"
            )
        capabilities = self._target_capability_projection(flow.target_id)
        action_capability = next(
            item for item in capabilities["actions"] if item["action"] == action.value
        )
        if not action_capability["supported"]:
            raise IntegrationLifecycleConflictError(str(action_capability["reason"]))

    def _validate_definition_deletion(
        self,
        flow: IntegrationFlowRecord,
        stored: Mapping[str, Any],
    ) -> int:
        catalog_id = str(flow.request.get("catalog_id") or "")
        entry = self.flows.catalog.get(catalog_id) if catalog_id else None
        if entry is None:
            raise IntegrationLifecycleConflictError(
                "integration has no deletable catalog definition"
            )
        if entry.source_type not in {CatalogSourceType.GIT, CatalogSourceType.LOCAL}:
            raise IntegrationLifecycleConflictError(
                "only user-owned Git or local definitions can be deleted"
            )
        dependents = [
            candidate.id
            for candidate in self.flows.list()
            if candidate.request.get("catalog_id") == catalog_id
            and self._state_projection(candidate, stored["states"].get(candidate.id))[
                "state"
            ]
            in {
                IntegrationLifecycle.ENABLED.value,
                IntegrationLifecycle.DISABLED.value,
            }
        ]
        if dependents:
            raise IntegrationLifecycleConflictError(
                "definition has installed dependents; uninstall them first"
            )
        return self.flows.catalog.snapshot().revision

    def _apply_effect(
        self,
        flow: IntegrationFlowRecord,
        action: IntegrationLifecycleAction,
        state: Mapping[str, Any],
        *,
        authority: str,
        allow_user_home: bool,
        catalog_revision: int | None,
    ) -> None:
        if action in {
            IntegrationLifecycleAction.ENABLE,
            IntegrationLifecycleAction.DISABLE,
        }:
            return
        if action is IntegrationLifecycleAction.UNINSTALL:
            receipt_id = state.get("receipt_id")
            if not isinstance(receipt_id, str) or not receipt_id:
                raise IntegrationLifecycleConflictError(
                    "installed integration receipt is missing"
                )
            self.flows.uninstall_owned(
                flow.id,
                receipt_id=receipt_id,
                authority=authority,
                allow_user_home=allow_user_home,
            )
            return
        catalog_id = str(flow.request.get("catalog_id") or "")
        if catalog_revision is None:
            raise IntegrationLifecycleConflictError("catalog revision is missing")
        try:
            self.flows.catalog.delete_definition(
                catalog_id,
                expected_revision=catalog_revision,
            )
        except CatalogConflictError as exc:
            raise IntegrationLifecycleConflictError(str(exc)) from exc

    def _record_failure(
        self,
        operation_id: str,
        applied: Sequence[tuple[str, dict[str, Any]]],
        *,
        cause: Exception,
    ) -> dict[str, Any]:
        with exclusive_file_lock(self.lock_path):
            stored = self._read_unlocked()
            operation = stored["operations"][operation_id]
            reversible = operation["action"] in {
                IntegrationLifecycleAction.ENABLE.value,
                IntegrationLifecycleAction.DISABLE.value,
            }
            if reversible:
                for flow_id, before in reversed(applied):
                    current = stored["states"].get(flow_id)
                    restored = {
                        **before,
                        "revision": int(current["revision"]) + 1 if current else 1,
                        "last_operation_id": operation_id,
                        "updated_at": self._timestamp(),
                    }
                    stored["states"][flow_id] = restored
                operation["status"] = (
                    IntegrationLifecycleOperationStatus.COMPENSATED.value
                )
                operation["recovery_actions"] = []
            elif applied:
                operation["status"] = (
                    IntegrationLifecycleOperationStatus.PARTIAL_FAILURE.value
                )
                remaining = [
                    flow_id
                    for flow_id in operation["flow_ids"]
                    if flow_id not in operation["completed_flow_ids"]
                ]
                operation["recovery_actions"] = [
                    f"retry-safe-{operation['action']}:{flow_id}"
                    for flow_id in remaining
                ]
            else:
                operation["status"] = IntegrationLifecycleOperationStatus.FAILED.value
                operation["recovery_actions"] = [
                    f"retry-safe-{operation['action']}:{operation['target_id']}"
                ]
            operation["error_code"] = type(cause).__name__
            operation["receipt_id"] = f"lrec_{_json_hash(operation)[:32]}"
            operation["updated_at"] = self._timestamp()
            stored["operations"][operation_id] = operation
            self._write_unlocked(stored)
        if isinstance(cause, IntegrationLifecycleConflictError):
            raise cause
        return self._operation_result(operation)

    def _next_state(
        self,
        before: Mapping[str, Any],
        action: IntegrationLifecycleAction,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        state = {
            IntegrationLifecycleAction.ENABLE: IntegrationLifecycle.ENABLED,
            IntegrationLifecycleAction.DISABLE: IntegrationLifecycle.DISABLED,
            IntegrationLifecycleAction.UNINSTALL: IntegrationLifecycle.UNINSTALLED,
            IntegrationLifecycleAction.DELETE_DEFINITION: (
                IntegrationLifecycle.DEFINITION_DELETED
            ),
        }[action]
        return {
            **before,
            "state": state.value,
            "enabled": state is IntegrationLifecycle.ENABLED,
            "installed": state
            in {IntegrationLifecycle.ENABLED, IntegrationLifecycle.DISABLED},
            "revision": int(before["revision"]) + 1,
            "last_operation_id": operation_id,
            "updated_at": self._timestamp(),
        }
