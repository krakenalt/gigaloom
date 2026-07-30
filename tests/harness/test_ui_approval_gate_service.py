from fastapi import HTTPException
from gpt2giga_harness.runtime.policy import (
    REVIEWED_PROMOTION_APPLY_OWNER,
    PermissionAction,
    PolicyEngine,
)
from gpt2giga_harness.runtime.store import RuntimeCoordinationStore
from gpt2giga_harness.sessions import FilesystemHarnessSessionStore
from gpt2giga_harness.types import (
    GigaChatApiMode,
    HarnessCapability,
)
from gpt2giga_harness.ui.services.approvals import ApprovalGateService


def test_approval_gate_deduplicates_requested_event(tmp_path):
    sessions = FilesystemHarnessSessionStore(tmp_path)
    runtime = RuntimeCoordinationStore(tmp_path)
    service = ApprovalGateService(
        policy_engine=PolicyEngine(runtime),
        runtime_store=runtime,
        session_store=sessions,
    )
    run = _run(sessions)

    first = service.gate(
        PermissionAction.GIT_APPLY,
        run,
        reason="Apply reviewed patch.",
        preview={"source_sha": "a" * 40, "patch_sha256": "b" * 64},
        approval_binding="reviewed-binding",
        enforcement_owner=REVIEWED_PROMOTION_APPLY_OWNER,
    )
    second = service.gate(
        PermissionAction.GIT_APPLY,
        run,
        reason="Apply reviewed patch.",
        preview={"source_sha": "a" * 40, "patch_sha256": "b" * 64},
        approval_binding="reviewed-binding",
        enforcement_owner=REVIEWED_PROMOTION_APPLY_OWNER,
    )

    assert first is not None
    assert second is not None
    assert first.status_code == second.status_code == 202
    assert first.body == second.body
    requested = [
        event
        for event in sessions.list_events(run.session_id, run_id=run.id)
        if event.type == "approval_requested"
    ]
    assert len(requested) == 1
    assert requested[0].payload["approval_id"] in first.body.decode()
    assert requested[0].trace_id == run.id
    assert requested[0].span_kind == "approval"
    assert requested[0].span_status == "pending"


def test_approval_gate_requires_durable_runtime(tmp_path):
    sessions = FilesystemHarnessSessionStore(tmp_path)
    service = ApprovalGateService(
        policy_engine=PolicyEngine(None),
        runtime_store=None,
        session_store=sessions,
    )

    try:
        service.gate(
            PermissionAction.GIT_APPLY,
            _run(sessions),
            reason="Apply reviewed patch.",
            preview={},
        )
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail == "Durable runtime is required for policy-gated actions"
    else:
        raise AssertionError("approval gate accepted an action without durable runtime")


def _run(sessions: FilesystemHarnessSessionStore):
    session = sessions.create_session(
        title="approval",
        metadata={"project_id": "project_approval"},
    )
    return sessions.create_run(
        session_id=session.id,
        harness_id="echo",
        prompt="review",
        model=None,
        api_mode=GigaChatApiMode.V2,
        capability=HarnessCapability.CHAT_COMPLETIONS,
        mode="edit",
        workspace=None,
    )
