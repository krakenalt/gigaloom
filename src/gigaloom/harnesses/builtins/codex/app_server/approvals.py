"""Durable approval bridge for supervised Codex app-server sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import TYPE_CHECKING, Any, Mapping

from gigaloom.harnesses.ports import (
    ApprovalStatus,
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    PolicyResolution,
)
from gigaloom.tools import THREAD_RELAY_APPROVAL_OWNER
from gigaloom.types import HarnessContext, HarnessEvent, HarnessRequest

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_APPROVAL_OWNER,
    APP_SERVER_APPROVAL_POLL_SECONDS,
)
from gigaloom.harnesses.builtins.codex.app_server.protocol import (
    _approval_contract,
    _provider_approval_binding,
)
from gigaloom.harnesses.builtins.codex.app_server.utils import (
    _cancel_requested,
    _mapping,
    _optional_text,
    _publish,
)

if TYPE_CHECKING:
    from gigaloom.harnesses.builtins.codex.app_server.session import (
        CodexAppServerSupervisor,
    )


def await_durable_approval(
    supervisor: CodexAppServerSupervisor,
    request: HarnessRequest,
    context: HarnessContext,
    collected: list[HarnessEvent],
    provider_request: Mapping[str, Any],
) -> str:
    """Persist one provider request and await its Approval Center decision."""
    method = str(provider_request.get("method") or "")
    params = _mapping(provider_request.get("params"))
    request_id = provider_request.get("id")
    thread_relay = _mapping(provider_request.get("thread_relay_approval"))
    if method == "item/tool/call" and thread_relay:
        action = PermissionAction.MCP_TOOL_CALL
        reason = "Agent proposes sending a message to another project chat."
        preview = _mapping(thread_relay.get("preview"))
        approval_binding = str(thread_relay.get("approval_binding") or "")
        project_id = _optional_text(thread_relay.get("project_id"))
        if (
            thread_relay.get("enforcement_owner") != THREAD_RELAY_APPROVAL_OWNER
            or len(approval_binding) != 64
            or any(
                character not in "0123456789abcdef" for character in approval_binding
            )
            or project_id is None
        ):
            raise ValueError("Thread Relay approval contract is invalid")
        policy_source = "thread_relay:agent_send"
        enforcement_owner = THREAD_RELAY_APPROVAL_OWNER
        run_id = None
        job_id = None
    else:
        action, reason, preview = _approval_contract(method, params)
        approval_binding = _provider_approval_binding(method, request_id, params)
        policy_source = "codex_app_server:on_request"
        enforcement_owner = APP_SERVER_APPROVAL_OWNER
        project_id = _optional_text(request.extra.get("project_id"))
        run_id = request.run_id
        runtime = _mapping(request.extra.get("runtime"))
        job_id = _optional_text(runtime.get("job_id"))
    existing = supervisor.runtime_store.find_approval_request_by_binding(
        approval_binding
    )
    timeout_seconds = max(
        min(
            float(provider_request.get("timeout_seconds") or 0.0),
            max(context.timeout_seconds, 0.1),
        ),
        0.1,
    )
    if existing is None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        ).isoformat()
        approval = supervisor.runtime_store.create_approval_request(
            PolicyResolution(
                action=action,
                decision=PolicyDecision.ASK,
                enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
                policy_source=policy_source,
            ),
            PolicyContext(
                project_id=project_id,
                session_id=request.session_id,
                run_id=run_id,
                job_id=job_id,
                reason=reason,
                preview=preview,
                approval_binding=approval_binding,
                enforcement_owner=enforcement_owner,
            ),
            expires_at=expires_at,
        )
    else:
        approval = existing
    with supervisor._active_driver_lock:
        active_driver = (
            supervisor._active_drivers.get(request.session_id)
            if request.session_id is not None
            else None
        )
    if active_driver is not None:
        active_driver.bind_durable_approval(str(request_id), approval.id)
    _publish(
        request,
        collected,
        HarnessEvent(
            type="approval_requested",
            message="Codex app-server is waiting for Approval Center.",
            payload={
                "approval_id": approval.id,
                "action": approval.action.value,
                "method": method,
                "reused": existing is not None,
            },
        ),
    )
    deadline = time.monotonic() + timeout_seconds
    while True:
        current = supervisor.runtime_store.get_approval_request(approval.id)
        if current.status is ApprovalStatus.APPROVED:
            decision = "accept"
            break
        if current.status in {
            ApprovalStatus.DENIED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELED,
        }:
            decision = "decline"
            break
        if _cancel_requested(request.cancel_event):
            decision = "cancel"
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            decision = "decline"
            break
        time.sleep(min(APP_SERVER_APPROVAL_POLL_SECONDS, remaining))
    _publish(
        request,
        collected,
        HarnessEvent(
            type="approval_decided",
            message="Codex app-server approval received a durable outcome.",
            payload={
                "approval_id": approval.id,
                "action": approval.action.value,
                "decision": decision,
            },
        ),
    )
    return decision
