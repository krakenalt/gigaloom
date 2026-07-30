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

from gpt2giga_harness.integration_catalog import (
    CatalogConflictError,
    CatalogSourceType,
)
from gpt2giga_harness.integration_flows import (
    BUILTIN_FLOW_TARGETS,
    IntegrationFlowRecord,
    IntegrationFlowService,
    IntegrationFlowStatus,
)
from gpt2giga_harness.integration_groups import (
    GroupedIntegrationService,
    IntegrationGroupStatus,
)
from gpt2giga_harness.integration_packages import IntegrationComponentType
from gpt2giga_harness.integration_runtime import IntegrationRuntimeStore
from gpt2giga_harness.diagnostics.inventory.capabilities import IntegrationLifecycle
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_LIFECYCLE_SCHEMA_VERSION = 1
MAX_LIFECYCLE_OPERATIONS = 500
_OPERATION_ID_RE = re.compile(r"iop_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
from .lifecycle_models import *  # noqa: F403
from .lifecycle_support import *  # noqa: F403


class _LifecycleCoreMixin:
    """Implementation slice for integration lifecycle operations."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        flow_service: IntegrationFlowService,
        group_service: GroupedIntegrationService,
        runtime_store: IntegrationRuntimeStore | None = None,
        now: Callable[[], datetime] | None = None,
        fault_injector: Callable[[str, str], None] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.root = self.data_dir / "integrations"
        self.path = self.root / "lifecycle.json"
        self.lock_path = self.root / ".lifecycle.json.lock"
        self.flows = flow_service
        self.groups = group_service
        self.runtime = runtime_store or IntegrationRuntimeStore(self.data_dir)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._fault_injector = fault_injector

    def inventory(self) -> dict[str, Any]:
        """Return effective installations and a source-derived capability matrix."""
        stored = self._read()
        installations = [
            self._state_projection(flow, stored["states"].get(flow.id))
            for flow in self.flows.list()
            if flow.status
            in {
                IntegrationFlowStatus.VERIFIED,
                IntegrationFlowStatus.ROLLED_BACK,
            }
        ]
        return {
            "schema_version": INTEGRATION_LIFECYCLE_SCHEMA_VERSION,
            "installations": installations,
            "capability_matrix": [
                self._target_capability_projection(target.id)
                for target in BUILTIN_FLOW_TARGETS
            ],
            "operations": [
                self._public_operation(item)
                for item in sorted(
                    stored["operations"].values(),
                    key=lambda value: str(value["updated_at"]),
                    reverse=True,
                )[:MAX_LIFECYCLE_OPERATIONS]
            ],
            "content_free": True,
        }

    def preview_flow(
        self,
        flow_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Persist one exact lifecycle preview for an installed flow."""
        flow = self.flows.get(flow_id)
        parsed_action = self._parse_action(action)
        return self._preview(
            kind="flow",
            target_id=flow_id,
            flows=(flow,),
            action=parsed_action,
        )

    def preview_group(
        self,
        group_id: str,
        action: str,
    ) -> dict[str, Any]:
        """Persist one exact lifecycle preview for every verified group child."""
        parsed_action = self._parse_action(action)
        if parsed_action is IntegrationLifecycleAction.DELETE_DEFINITION:
            raise ValueError("group definition deletion is not supported")
        group = self.groups.get(group_id)
        if group.status is not IntegrationGroupStatus.VERIFIED:
            raise IntegrationLifecycleConflictError(
                "group lifecycle requires a verified installation"
            )
        flows = tuple(self.flows.get(item.flow_id) for item in group.children)
        return self._preview(
            kind="group",
            target_id=group_id,
            flows=flows,
            action=parsed_action,
        )

    def apply(
        self,
        operation_id: str,
        *,
        plan_id: str,
        authority: str,
        expected_revisions: Mapping[str, int],
        confirm_id: str | None = None,
        allow_user_home: bool = False,
    ) -> dict[str, Any]:
        """Apply one exact operation and return a durable recovery receipt."""
        _validate_operation_id(operation_id)
        _validate_plan_id(plan_id)
        _validate_identity(authority, field_name="lifecycle authority")
        if not isinstance(expected_revisions, Mapping):
            raise ValueError("expected_revisions must be an object")
        with exclusive_file_lock(self.lock_path):
            stored = self._read_unlocked()
            operation = stored["operations"].get(operation_id)
            if operation is None:
                raise IntegrationLifecycleNotFoundError(operation_id)
            if operation["plan_id"] != plan_id:
                raise IntegrationLifecycleConflictError(
                    "lifecycle approval does not match the preview"
                )
            if (
                operation["status"]
                == IntegrationLifecycleOperationStatus.SUCCEEDED.value
            ):
                return self._operation_result(operation)
            if operation["status"] not in {
                IntegrationLifecycleOperationStatus.AWAITING_APPROVAL.value,
                IntegrationLifecycleOperationStatus.PARTIAL_FAILURE.value,
                IntegrationLifecycleOperationStatus.FAILED.value,
            }:
                raise IntegrationLifecycleConflictError(
                    "lifecycle operation cannot be applied in its state"
                )
            expected = {
                str(key): _revision(value) for key, value in expected_revisions.items()
            }
            if expected != operation["expected_revisions"]:
                raise IntegrationLifecycleConflictError(
                    "lifecycle revisions do not match the preview"
                )
            if operation["confirmation_required"]:
                if confirm_id != operation["confirmation_id"]:
                    raise IntegrationLifecycleConflictError(
                        "exact lifecycle confirmation is required"
                    )
            for flow_id, revision in expected.items():
                if flow_id in operation["completed_flow_ids"]:
                    continue
                flow = self.flows.get(flow_id)
                state = self._state_projection(flow, stored["states"].get(flow_id))
                if state["revision"] != revision:
                    raise IntegrationLifecycleConflictError(
                        "integration lifecycle changed after preview"
                    )
            operation["status"] = IntegrationLifecycleOperationStatus.APPLYING.value
            operation["authority_sha256"] = _json_hash({"authority": authority})
            operation["updated_at"] = self._timestamp()
            stored["operations"][operation_id] = operation
            self._write_unlocked(stored)

        applied: list[tuple[str, dict[str, Any]]] = []
        try:
            for flow_id in operation["flow_ids"]:
                if flow_id in operation["completed_flow_ids"]:
                    continue
                with exclusive_file_lock(self.lock_path):
                    stored = self._read_unlocked()
                    flow = self.flows.get(flow_id)
                    before = self._state_projection(flow, stored["states"].get(flow_id))
                if (
                    operation["action"] == IntegrationLifecycleAction.UNINSTALL.value
                    and self._active_sessions(flow)
                ):
                    raise IntegrationLifecycleConflictError(
                        "active sessions retain this revision; uninstall is blocked"
                    )
                if self._fault_injector is not None:
                    self._fault_injector(operation["action"], flow_id)
                self._apply_effect(
                    flow,
                    IntegrationLifecycleAction(operation["action"]),
                    before,
                    authority=authority,
                    allow_user_home=allow_user_home,
                    catalog_revision=operation.get("catalog_revision"),
                )
                after = self._next_state(
                    before,
                    IntegrationLifecycleAction(operation["action"]),
                    operation_id=operation_id,
                )
                with exclusive_file_lock(self.lock_path):
                    stored = self._read_unlocked()
                    current = self._state_projection(
                        flow, stored["states"].get(flow_id)
                    )
                    if current["revision"] != before["revision"]:
                        raise IntegrationLifecycleConflictError(
                            "integration lifecycle changed during apply"
                        )
                    stored["states"][flow_id] = after
                    operation = stored["operations"][operation_id]
                    operation["completed_flow_ids"] = [
                        *operation["completed_flow_ids"],
                        flow_id,
                    ]
                    operation["updated_at"] = self._timestamp()
                    stored["operations"][operation_id] = operation
                    self._write_unlocked(stored)
                applied.append((flow_id, before))
        except Exception as exc:
            return self._record_failure(operation_id, applied, cause=exc)

        with exclusive_file_lock(self.lock_path):
            stored = self._read_unlocked()
            operation = stored["operations"][operation_id]
            operation["status"] = IntegrationLifecycleOperationStatus.SUCCEEDED.value
            operation["recovery_actions"] = []
            operation["error_code"] = None
            operation["receipt_id"] = f"lrec_{_json_hash(operation)[:32]}"
            operation["updated_at"] = self._timestamp()
            stored["operations"][operation_id] = operation
            self._write_unlocked(stored)
        return self._operation_result(operation)

    def admitted_flows(self) -> tuple[IntegrationFlowRecord, ...]:
        """Return only enabled installed revisions for newly created sessions."""
        stored = self._read()
        return tuple(
            flow
            for flow in self.flows.list()
            if self._state_projection(flow, stored["states"].get(flow.id))["state"]
            == IntegrationLifecycle.ENABLED.value
        )
