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
from gpt2giga_harness.product_capabilities import IntegrationLifecycle
from gpt2giga_harness.sessions import locking as _session_locking

exclusive_file_lock = _session_locking.exclusive_file_lock


INTEGRATION_LIFECYCLE_SCHEMA_VERSION = 1
MAX_LIFECYCLE_OPERATIONS = 500
_OPERATION_ID_RE = re.compile(r"iop_[0-9a-f]{32}\Z")
_PLAN_ID_RE = re.compile(r"plan_[0-9a-f]{64}\Z")
_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+~-]{0,255}\Z")
from .lifecycle_models import *  # noqa: F403
from .lifecycle_support import *  # noqa: F403


class _LifecycleProjectionMixin:
    """Implementation slice for integration lifecycle operations."""

    def _state_projection(
        self,
        flow: IntegrationFlowRecord,
        stored: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if stored is not None:
            return dict(stored)
        if flow.status is IntegrationFlowStatus.VERIFIED:
            state = IntegrationLifecycle.ENABLED
        elif flow.status is IntegrationFlowStatus.ROLLED_BACK:
            state = IntegrationLifecycle.UNINSTALLED
        else:
            state = IntegrationLifecycle.DEFINITION_ONLY
        return {
            "flow_id": flow.id,
            "package_id": flow.package_id,
            "package_version": flow.package_version,
            "target_id": flow.target_id,
            "scope": flow.scope.value,
            "state": state.value,
            "enabled": state is IntegrationLifecycle.ENABLED,
            "installed": state
            in {IntegrationLifecycle.ENABLED, IntegrationLifecycle.DISABLED},
            "revision": 1,
            "receipt_id": flow.receipt_id,
            "catalog_id": flow.request.get("catalog_id"),
            "last_operation_id": None,
            "updated_at": flow.updated_at,
            "content_free": True,
        }

    def _target_capability_projection(self, target_id: str) -> dict[str, Any]:
        target = next(item for item in BUILTIN_FLOW_TARGETS if item.id == target_id)
        executable = target_id != "harness-adapter-package"
        component = (
            target.component_types[0].value if target.component_types else "unknown"
        )
        actions = []
        for action in IntegrationLifecycleAction:
            supported = executable
            reason: str | None = None
            if action is IntegrationLifecycleAction.DELETE_DEFINITION:
                supported = True
            elif action is IntegrationLifecycleAction.UNINSTALL and component not in {
                IntegrationComponentType.SKILL.value,
                IntegrationComponentType.MCP.value,
                IntegrationComponentType.PLUGIN.value,
            }:
                supported = False
                reason = "target has no application-owned uninstall surface"
            elif not executable:
                supported = False
                reason = "target lifecycle remains provider-owned"
            actions.append(
                {
                    "action": action.value,
                    "supported": supported,
                    "reason": reason,
                }
            )
        actions.append(
            {
                "action": "rollback",
                "supported": "rollback" in target.capabilities
                or component == IntegrationComponentType.SKILL.value,
                "reason": None,
            }
        )
        return {
            "target_id": target_id,
            "component_types": [item.value for item in target.component_types],
            "actions": actions,
            "content_free": True,
        }

    def _active_sessions(self, flow: IntegrationFlowRecord) -> list[dict[str, Any]]:
        return list(
            self.runtime.bindings_for_integration(
                package_id=flow.package_id,
                target_id=flow.target_id,
            )
        )

    def _effect_projection(
        self,
        flow: IntegrationFlowRecord,
        action: IntegrationLifecycleAction,
        active_sessions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return {
            "flow_id": flow.id,
            "package_id": flow.package_id,
            "target_id": flow.target_id,
            "scope": flow.scope.value,
            "mutation": (
                "admission_state_only"
                if action
                in {
                    IntegrationLifecycleAction.ENABLE,
                    IntegrationLifecycleAction.DISABLE,
                }
                else (
                    "installer_owned_material_only"
                    if action is IntegrationLifecycleAction.UNINSTALL
                    else "user_owned_definition_only"
                )
            ),
            "active_session_count": len(active_sessions),
            "content_free": True,
        }

    def _public_operation(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": operation["id"],
            "plan_id": operation["plan_id"],
            "kind": operation["kind"],
            "target_id": operation["target_id"],
            "action": operation["action"],
            "flow_ids": list(operation["flow_ids"]),
            "expected_revisions": dict(operation["expected_revisions"]),
            "status": operation["status"],
            "confirmation_required": operation["confirmation_required"],
            "confirmation_id": operation["confirmation_id"],
            "active_session_count": sum(
                len(items) for items in operation["active_sessions"].values()
            ),
            "completed_flow_ids": list(operation["completed_flow_ids"]),
            "receipt_id": operation["receipt_id"],
            "recovery_actions": list(operation["recovery_actions"]),
            "error_code": operation["error_code"],
            "created_at": operation["created_at"],
            "updated_at": operation["updated_at"],
            "content_free": True,
        }

    def _operation_result(self, operation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "operation": self._public_operation(operation),
            "receipt": {
                "id": operation["receipt_id"],
                "action": operation["action"],
                "outcome": operation["status"],
                "completed_flow_ids": list(operation["completed_flow_ids"]),
                "recovery_actions": list(operation["recovery_actions"]),
                "content_free": True,
            },
        }

    def _parse_action(self, value: str) -> IntegrationLifecycleAction:
        try:
            return IntegrationLifecycleAction(value)
        except ValueError as exc:
            raise ValueError("integration lifecycle action is invalid") from exc

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _read(self) -> dict[str, Any]:
        self._ensure_root()
        with exclusive_file_lock(self.lock_path):
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": INTEGRATION_LIFECYCLE_SCHEMA_VERSION,
                "states": {},
                "operations": {},
            }
        if self.path.is_symlink() or not self.path.is_file():
            raise IntegrationLifecycleError("integration lifecycle state is unsafe")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise IntegrationLifecycleError(
                "integration lifecycle state is unreadable"
            ) from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != INTEGRATION_LIFECYCLE_SCHEMA_VERSION
            or not isinstance(payload.get("states"), Mapping)
            or not isinstance(payload.get("operations"), Mapping)
        ):
            raise IntegrationLifecycleError(
                "integration lifecycle state schema is unsupported"
            )
        return {
            "schema_version": INTEGRATION_LIFECYCLE_SCHEMA_VERSION,
            "states": {
                str(key): dict(value) for key, value in payload["states"].items()
            },
            "operations": {
                str(key): dict(value) for key, value in payload["operations"].items()
            },
        }

    def _write_unlocked(self, payload: Mapping[str, Any]) -> None:
        self._ensure_root()
        raw = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
        handle, temporary = tempfile.mkstemp(
            prefix=".lifecycle-",
            suffix=".tmp",
            dir=self.root,
            text=True,
        )
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            with suppress(OSError):
                os.close(handle)
            with suppress(OSError):
                os.unlink(temporary)
            raise

    def _ensure_root(self) -> None:
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise IntegrationLifecycleError("integration lifecycle root is unsafe")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
