"""Durable approval bridge for supervised Codex app-server sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import TYPE_CHECKING, Any, Mapping

from gpt2giga_harness.harnesses.ports import (
    ApprovalStatus,
    EnforcementLevel,
    PolicyContext,
    PolicyDecision,
    PolicyResolution,
)
from gpt2giga_harness.types import HarnessContext, HarnessEvent, HarnessRequest

from gpt2giga_harness.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_APPROVAL_OWNER,
    APP_SERVER_APPROVAL_POLL_SECONDS,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.protocol import (
    _approval_contract,
    _provider_approval_binding,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.utils import (
    _cancel_requested,
    _mapping,
    _optional_text,
    _publish,
)

if TYPE_CHECKING:
    from gpt2giga_harness.harnesses.builtins.codex.app_server.session import (
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
    action, reason, preview = _approval_contract(method, params)
    approval_binding = _provider_approval_binding(method, request_id, params)
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
        runtime = _mapping(request.extra.get("runtime"))
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        ).isoformat()
        approval = supervisor.runtime_store.create_approval_request(
            PolicyResolution(
                action=action,
                decision=PolicyDecision.ASK,
                enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
                policy_source="codex_app_server:on_request",
            ),
            PolicyContext(
                project_id=_optional_text(request.extra.get("project_id")),
                session_id=request.session_id,
                run_id=request.run_id,
                job_id=_optional_text(runtime.get("job_id")),
                reason=reason,
                preview=preview,
                approval_binding=approval_binding,
                enforcement_owner=APP_SERVER_APPROVAL_OWNER,
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
