"""FastAPI app for the minimal Unified Harness browser UI."""

from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import replace
from time import monotonic
from typing import Any, Mapping

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse

from gpt2giga_harness.config import (
    HarnessConfig,
)
from gpt2giga_harness.environment_actions import (
    EnvironmentCommitService,
)
from gpt2giga_harness.environment_pull_requests import (
    EnvironmentPullRequestService,
)
from gpt2giga_harness.environment_push import (
    EnvironmentPushService,
)
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.github_environments import GitHubEnvironmentService
from gpt2giga_harness.integration_flows import IntegrationFlowService
from gpt2giga_harness.integration_groups import GroupedIntegrationService
from gpt2giga_harness.integration_lifecycle import IntegrationLifecycleService
from gpt2giga_harness.native.process import (
    NativeProcessManager,
)
from gpt2giga_harness.native.registry import (
    NativeHistoryConnectorRegistry,
)
from gpt2giga_harness.native.store import (
    NativeSessionIndexStore,
)
from gpt2giga_harness.pr_artifacts import (
    build_pr_artifact,
    create_pr_branch,
    pr_artifact_to_dict,
)
from gpt2giga_harness.preflight import (
    build_preflight_report,
    format_preflight_block_message,
)
from gpt2giga_harness.provenance import (
    build_replay_request,
    run_provenance_to_dict,
)
from gpt2giga_harness.provider_account_sessions import (
    ProviderAccountSessionError,
)
from gpt2giga_harness.provider_authentication_broker import NativeLoginBroker
from gpt2giga_harness.provider_settings import ProviderSettingsService
from gpt2giga_harness.registry import HarnessRegistry
from gpt2giga_harness.runtime.models import job_to_dict
from gpt2giga_harness.runtime.policy import (
    INTERACTIVE_PROFILE,
    REVIEWED_PROMOTION_APPLY_OWNER,
    REVIEWED_PROMOTION_BRANCH_OWNER,
    EnforcementLevel,
    PermissionAction,
    PolicyContext,
    PolicyDecision,
    approval_request_to_dict,
)
from gpt2giga_harness.runtime.store import JobNotFoundError, RuntimeCoordinationStore
from gpt2giga_harness.sessions import (
    HarnessSessionStore,
    RunNotFoundError,
    SessionNotFoundError,
)
from gpt2giga_harness.sessions.event_stream import (
    StreamCapacityError,
    StreamSignal,
)
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessSession,
    HarnessStoredEvent,
    bundle_to_dict,
    run_to_dict,
    session_to_dict,
)
from gpt2giga_harness.sessions.store import new_id, title_from_prompt, utc_now
from gpt2giga_harness.skill_library import SkillLibraryService
from gpt2giga_harness.types import (
    GigaChatApiMode,
    HarnessCapability,
    HarnessEventType,
    HarnessRequest,
    parse_api_mode,
    parse_builtin_tools,
    parse_capability,
    result_to_dict,
)
from gpt2giga_harness.ui.async_execution import (
    AsyncDiagnosticsMiddleware,
    ConformantAPIRoute,
    async_handler_contract_errors,
    run_in_threadpool,
    run_stream_offload,
)
from gpt2giga_harness.ui.container import build_app_services
from gpt2giga_harness.ui.dependencies import install_app_services
from gpt2giga_harness.ui.execution_contracts import install_execution_contracts
from gpt2giga_harness.ui.mutation_contracts import install_mutation_contracts
from gpt2giga_harness.ui.remote_identity import (
    RemoteIdentityError,
    RemoteOIDCClient,
    RemoteOIDCSettings,
)
from gpt2giga_harness.ui.routers.agents import router as agents_router
from gpt2giga_harness.ui.routers.approvals import router as approvals_router
from gpt2giga_harness.ui.routers.arena import (
    create_router as create_arena_router,
)
from gpt2giga_harness.ui.routers.attachments import (
    create_router as create_attachments_router,
)
from gpt2giga_harness.ui.routers.automation import router as automation_router
from gpt2giga_harness.ui.routers.catalog import create_router as create_catalog_router
from gpt2giga_harness.ui.routers.cockpit import router as cockpit_router
from gpt2giga_harness.ui.routers.compatibility import router as compatibility_router
from gpt2giga_harness.ui.routers.editor import create_router as create_editor_router
from gpt2giga_harness.ui.routers.environments import router as environments_router
from gpt2giga_harness.ui.routers.evals import create_router as create_evals_router
from gpt2giga_harness.ui.routers.evaluate import router as evaluate_router
from gpt2giga_harness.ui.routers.files import create_file_preview_router
from gpt2giga_harness.ui.routers.handoff_capsules import (
    router as handoff_capsules_router,
)
from gpt2giga_harness.ui.routers.integrations import router as integrations_router
from gpt2giga_harness.ui.routers.native_process_start import (
    create_router as create_native_process_start_router,
)
from gpt2giga_harness.ui.routers.native_processes import (
    create_router as create_native_processes_router,
)
from gpt2giga_harness.ui.routers.native_sessions import (
    create_router as create_native_sessions_router,
)
from gpt2giga_harness.ui.routers.project_memory import (
    create_router as create_project_memory_router,
)
from gpt2giga_harness.ui.routers.project_tools import (
    create_router as create_project_tools_router,
)
from gpt2giga_harness.ui.routers.projects import create_router as create_projects_router
from gpt2giga_harness.ui.routers.provider_handoffs import (
    create_provider_handoff_router,
)
from gpt2giga_harness.ui.routers.runs import router as runs_router
from gpt2giga_harness.ui.routers.schedules import router as schedules_router
from gpt2giga_harness.ui.routers.session_catalog import (
    create_router as create_session_catalog_router,
)
from gpt2giga_harness.ui.routers.settings import router as settings_router
from gpt2giga_harness.ui.routers.shell import create_shell_router
from gpt2giga_harness.ui.routers.tools import router as tools_router
from gpt2giga_harness.ui.routers.trace_replays import (
    router as trace_replays_router,
)
from gpt2giga_harness.ui.routers.tui_actions import (
    router as tui_actions_router,
)
from gpt2giga_harness.ui.routers.tui_actions import (
    validate_run_action_binding,
)
from gpt2giga_harness.ui.routers.workbench_resources import (
    router as workbench_resources_router,
)
from gpt2giga_harness.ui.routers.workbench_state import (
    router as workbench_state_router,
)
from gpt2giga_harness.ui.routers.workflows import router as workflows_router
from gpt2giga_harness.ui.security import (
    HarnessUISecurityMiddleware,
    is_loopback_host,
)
from gpt2giga_harness.ui.services import ActiveHeadlessRun
from gpt2giga_harness.ui.services.lifecycle import create_app_lifespan
from gpt2giga_harness.ui.services.navigation import (
    session_summary as _session_summary,
)
from gpt2giga_harness.ui.services.provenance import (
    _build_current_run_provenance,
    _latest_raw_request_for_run,
    _reviewed_evidence_for_run,
)
from gpt2giga_harness.ui.services.request_values import (
    optional_text as _optional_text,
)
from gpt2giga_harness.ui.streaming.events import (
    encode_run_stream_cursor as _encode_run_stream_cursor,
)
from gpt2giga_harness.ui.streaming.events import (
    event_response as _event_response,
)
from gpt2giga_harness.ui.streaming.events import (
    resolve_run_stream_cursor as _resolve_run_stream_cursor,
)
from gpt2giga_harness.ui.streaming.events import (
    run_resnapshot_sse as _run_resnapshot_sse,
)
from gpt2giga_harness.ui.streaming.events import (
    run_sse_event as _run_sse_event,
)
from gpt2giga_harness.ui.streaming.events import (
    run_status_is_terminal as _run_status_is_terminal,
)
from gpt2giga_harness.workspace import (
    resolve_workspace,
)
from gpt2giga_harness.worktrees import (
    WorktreeConflictError,
    WorktreeError,
    apply_run_diff,
    discard_run_worktree,
    open_worktree_response,
    review_run_diff,
    run_diff_response,
)

RUN_EVENT_STREAM_HEARTBEAT_SECONDS = 15.0
RUN_EVENT_STREAM_POLL_SECONDS = 0.1
TUI_NAVIGATION_TERMINAL_RE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)?|P.*?(?:\x1b\\|$)|\[[0-?]*[ -/]*[@-~]|[@-_])",
    re.DOTALL,
)
TUI_NAVIGATION_BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


def create_app(
    config: HarnessConfig | None = None,
    registry: HarnessRegistry | None = None,
    store: HarnessSessionStore | None = None,
    native_registry: NativeHistoryConnectorRegistry | None = None,
    native_index_store: NativeSessionIndexStore | None = None,
    native_process_manager: NativeProcessManager | None = None,
    runtime_store: RuntimeCoordinationStore | None = None,
    provider_settings_service: ProviderSettingsService | None = None,
    native_login_broker: NativeLoginBroker | None = None,
    integration_flow_service: IntegrationFlowService | None = None,
    grouped_integration_service: GroupedIntegrationService | None = None,
    integration_lifecycle_service: IntegrationLifecycleService | None = None,
    skill_library_service: SkillLibraryService | None = None,
    github_environment_service: GitHubEnvironmentService | None = None,
    environment_commit_service: EnvironmentCommitService | None = None,
    environment_push_service: EnvironmentPushService | None = None,
    environment_pull_request_service: EnvironmentPullRequestService | None = None,
    remote_oidc_client: RemoteOIDCClient | None = None,
) -> FastAPI:
    """Create the Unified Harness UI app."""
    config = config or HarnessConfig.from_env()
    validate_ui_bind(config, allow_remote=None)
    services = build_app_services(
        config=config,
        registry=registry,
        session_store=store,
        native_registry=native_registry,
        native_index_store=native_index_store,
        native_process_manager=native_process_manager,
        runtime_store=runtime_store,
        provider_settings_service=provider_settings_service,
        native_login_broker=native_login_broker,
        integration_flow_service=integration_flow_service,
        grouped_integration_service=grouped_integration_service,
        integration_lifecycle_service=integration_lifecycle_service,
        skill_library_service=skill_library_service,
        github_environment_service=github_environment_service,
        environment_commit_service=environment_commit_service,
        environment_push_service=environment_push_service,
        environment_pull_request_service=environment_pull_request_service,
        remote_oidc_client=remote_oidc_client,
    )
    registry = services.registry
    store = services.session_store
    runtime_store = services.runtime_store
    native_registry = services.native_registry
    native_index_store = services.native_index_store
    native_process_manager = services.native_process_manager
    runner = services.session_runner
    durable_dispatcher = services.job_dispatcher
    session_service = services.session_service
    policy_engine = services.policy_engine
    active_headless_runs = services.active_headless_runs
    async_diagnostics = services.async_diagnostics
    run_event_broker = services.run_event_broker

    app = FastAPI(
        title="gpt2giga Unified Harness",
        docs_url=None,
        redoc_url=None,
        lifespan=create_app_lifespan(services),
    )
    app.router.route_class = ConformantAPIRoute
    app.add_middleware(
        HarnessUISecurityMiddleware,
        security=services.ui_security,
    )
    app.add_middleware(
        AsyncDiagnosticsMiddleware,
        diagnostics=async_diagnostics,
    )
    install_app_services(app, services)

    def _approval_gate(
        action: PermissionAction,
        run: HarnessRun,
        *,
        reason: str,
        preview: Mapping[str, Any],
        approval_binding: str | None = None,
        enforcement_owner: str | None = None,
    ) -> JSONResponse | None:
        if runtime_store is None:
            raise HTTPException(
                status_code=409,
                detail="Durable runtime is required for policy-gated actions",
            )
        session = store.get_session(run.session_id)
        runtime_metadata = run.metadata.get("runtime")
        job_id = (
            str(runtime_metadata.get("job_id") or "") or None
            if isinstance(runtime_metadata, Mapping)
            else None
        )
        if job_id is not None:
            try:
                runtime_store.get_job(job_id)
            except JobNotFoundError:
                job_id = None
        context = PolicyContext(
            project_id=str(session.metadata.get("project_id") or "") or None,
            session_id=run.session_id,
            run_id=run.id,
            job_id=job_id,
            reason=reason,
            preview=preview,
            approval_binding=approval_binding,
            enforcement_owner=enforcement_owner,
        )
        resolution = policy_engine.resolve(
            action,
            profile=INTERACTIVE_PROFILE,
            context=context,
            enforcement=EnforcementLevel.ENFORCED_BY_HARNESS,
        )
        if resolution.decision is PolicyDecision.DENY:
            raise HTTPException(status_code=403, detail="Action denied by policy")
        if resolution.decision is PolicyDecision.ALLOW:
            return None
        approval = runtime_store.create_approval_request(resolution, context)
        existing = any(
            event.type == "approval_requested"
            and event.payload.get("approval_id") == approval.id
            for event in store.list_events(run.session_id, run_id=run.id)
        )
        if not existing:
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="approval_requested",
                    message=f"Approval required for {action.value}.",
                    payload={
                        "approval_id": approval.id,
                        "action": action.value,
                        "enforcement": resolution.enforcement.value,
                    },
                    created_at=utc_now(),
                    trace_id=context.job_id or run.id,
                    job_id=context.job_id,
                    span_kind="approval",
                    span_status="pending",
                )
            )
        return JSONResponse(
            status_code=202,
            content={
                "approval_required": True,
                "approval": approval_request_to_dict(approval),
            },
        )

    async def _start_headless_run(
        session_id: str,
        payload: Mapping[str, Any],
    ) -> HarnessRun:
        if durable_dispatcher is not None:
            idempotency_key = str(
                payload.get("idempotency_key") or f"ui_{new_id('submit')}"
            )
            submission = await run_in_threadpool(
                session_service.submit_turn,
                session_id,
                payload,
                idempotency_key=idempotency_key,
                origin="interactive",
            )
            return submission.queued.run
        existing_runs = await run_in_threadpool(store.list_runs, session_id)
        before_run_ids = {run.id for run in existing_runs}
        cancel_event = threading.Event()
        task = asyncio.create_task(
            run_in_threadpool(
                session_service.run_turn,
                session_id,
                payload,
                cancel_event=cancel_event,
            )
        )
        run = await _wait_for_started_run(
            store=store,
            session_id=session_id,
            before_run_ids=before_run_ids,
            task=task,
        )
        active_headless_runs[run.id] = ActiveHeadlessRun(
            task=task,
            cancel_event=cancel_event,
        )
        task.add_done_callback(
            lambda _task, run_id=run.id: active_headless_runs.pop(run_id, None)
        )
        return run

    def _run_start_response(run: HarnessRun) -> dict[str, Any]:
        events = session_service.list_run_events(run.id)
        payload = {
            "session": _session_summary(store, run.session_id),
            "run": run_to_dict(run),
            "events": [_event_response(event) for event in events],
            "stream_url": f"/api/runs/{run.id}/events/stream",
            "cancel_url": f"/api/runs/{run.id}/cancel",
        }
        job = session_service.find_job_for_run(run.id)
        if job is not None:
            payload["job"] = job_to_dict(job)
        return payload

    def _run_provenance_response(run: HarnessRun) -> dict[str, Any]:
        provenance = _build_current_run_provenance(
            store=store,
            registry=registry,
            config=config,
            run=run,
            runtime_store=runtime_store,
        )
        return {
            "run": run_to_dict(run),
            "provenance": run_provenance_to_dict(provenance),
        }

    @app.post("/api/sessions/run/start")
    async def create_session_and_start_run(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            session = await run_in_threadpool(
                session_service.create_session,
                payload,
                title_from_turn=False,
                validate_harness=True,
            )
            run = await _start_headless_run(session.id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return await run_in_threadpool(_run_start_response, run)

    @app.post("/api/sessions/{session_id}/run/start")
    async def start_run_in_session(
        session_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            await run_in_threadpool(store.get_session, session_id)
            run = await _start_headless_run(session_id, payload)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return await run_in_threadpool(_run_start_response, run)

    @app.get("/api/runs/{run_id}/events/stream")
    async def run_events_stream(
        run_id: str,
        after_id: str | None = Query(default=None),
        tail_only: bool = Query(default=False),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        try:
            initial_run = await run_stream_offload(session_service.get_run, run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        try:
            subscription = run_event_broker.subscribe(run_id)
        except StreamCapacityError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        try:
            cursor_position = await run_stream_offload(
                _resolve_run_stream_cursor,
                store,
                initial_run,
                _optional_text(last_event_id) or _optional_text(after_id),
                tail_only=tail_only,
            )
        except ValueError as exc:
            subscription.close()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        async def stream_events():
            current_offset = cursor_position.offset
            terminal_event_seen = cursor_position.terminal_seen
            next_heartbeat_at = monotonic() + RUN_EVENT_STREAM_HEARTBEAT_SECONDS
            try:
                while True:
                    try:
                        current_run, page = await run_stream_offload(
                            session_service.read_run_event_tail,
                            run_id,
                            current_offset,
                        )
                    except (RunNotFoundError, SessionNotFoundError, ValueError):
                        break
                    for item in page.items:
                        current_offset = item.next_offset
                        event = item.event
                        if event.type == HarnessEventType.RUN_FINISHED.value:
                            terminal_event_seen = True
                        cursor = _encode_run_stream_cursor(
                            current_run,
                            current_offset,
                            terminal_event_seen=terminal_event_seen,
                        )
                        yield _run_sse_event(event, cursor)
                    if page.next_offset > current_offset:
                        current_offset = page.next_offset
                    if page.has_more:
                        continue
                    if _run_status_is_terminal(current_run.status):
                        if terminal_event_seen:
                            break
                        terminal_event_seen = True
                        cursor = _encode_run_stream_cursor(
                            current_run,
                            current_offset,
                            terminal_event_seen=True,
                        )
                        yield _run_sse_event(
                            HarnessStoredEvent(
                                id=f"evt_terminal_{current_run.id}",
                                session_id=current_run.session_id,
                                run_id=current_run.id,
                                type=HarnessEventType.RUN_FINISHED.value,
                                message="Harness run reached a terminal state.",
                                payload={
                                    "status": current_run.status,
                                    "synthetic": True,
                                },
                                created_at=(
                                    current_run.finished_at
                                    or current_run.updated_at
                                    or current_run.created_at
                                ),
                            ),
                            cursor,
                        )
                        break
                    signal = await subscription.wait(RUN_EVENT_STREAM_POLL_SECONDS)
                    cursor = _encode_run_stream_cursor(
                        current_run,
                        current_offset,
                        terminal_event_seen=terminal_event_seen,
                    )
                    if signal is StreamSignal.RESNAPSHOT_REQUIRED:
                        yield _run_resnapshot_sse(current_run, cursor)
                    elif signal is None and monotonic() >= next_heartbeat_at:
                        yield ": heartbeat\n\n"
                        next_heartbeat_at = (
                            monotonic() + RUN_EVENT_STREAM_HEARTBEAT_SECONDS
                        )
            finally:
                subscription.close()

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(
        run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        validate_run_action_binding(run, payload)
        if _run_status_is_terminal(run.status):
            return {
                "cancel_requested": False,
                "active": False,
                "run": run_to_dict(run),
            }
        durable_job = (
            runtime_store.find_job_for_run(run.id)
            if runtime_store is not None
            else None
        )
        if durable_job is not None:
            job = runtime_store.request_cancel(durable_job.id)
            attempts = runtime_store.list_attempts(job.id)
            active_attempt = next(
                (attempt for attempt in reversed(attempts) if attempt.run_id == run.id),
                None,
            )
            if active_attempt is None and job.status.value == "queued":
                job = runtime_store.transition_job(
                    job.id, "canceled", expected_status="queued"
                )
                run = store.update_run(
                    run.id,
                    status="canceled",
                    finished_at=utc_now(),
                    error="Harness run canceled before worker claim.",
                    metadata={**dict(run.metadata), "cancel_requested": True},
                )
            else:
                run = store.update_run(
                    run.id,
                    metadata={**dict(run.metadata), "cancel_requested": True},
                )
            if not bool(run.metadata.get("cancel_event_recorded")):
                store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.CANCEL_REQUESTED.value,
                        message="Harness run cancellation requested.",
                        payload={
                            "job_id": job.id,
                            "active": active_attempt is not None,
                        },
                        created_at=utc_now(),
                        trace_id=job.id,
                        job_id=job.id,
                        attempt_id=active_attempt.id if active_attempt else None,
                    )
                )
            return {
                "cancel_requested": True,
                "active": active_attempt is not None,
                "job": job_to_dict(job),
                "run": run_to_dict(run),
            }
        active = active_headless_runs.get(run.id)
        if active is not None and active.task.done():
            active = None
        # A headless task can finish and remove itself from the active map after
        # the first read. Re-read before applying a synthetic cancellation so a
        # completed run can never be overwritten with a stale canceled status.
        run = store.get_run(run.id)
        if _run_status_is_terminal(run.status):
            return {
                "cancel_requested": False,
                "active": False,
                "run": run_to_dict(run),
            }
        already_requested = bool(run.metadata.get("cancel_requested"))
        if active is not None:
            active.cancel_event.set()
            metadata = {**dict(run.metadata), "cancel_requested": True}
            run = store.update_run(run.id, metadata=metadata)
        else:
            metadata = {**dict(run.metadata), "cancel_requested": True}
            run = store.update_run(
                run.id,
                status="canceled",
                finished_at=utc_now(),
                error="Harness run canceled.",
                metadata=metadata,
            )
        if not already_requested:
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type=HarnessEventType.CANCEL_REQUESTED.value,
                    message="Harness run cancellation requested.",
                    payload={"active": active is not None},
                    created_at=utc_now(),
                )
            )
            if active is None:
                store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.RUN_CANCELED.value,
                        message="Harness run canceled.",
                        payload={},
                        created_at=utc_now(),
                    )
                )
                store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=run.session_id,
                        run_id=run.id,
                        type=HarnessEventType.RUN_FINISHED.value,
                        message="Harness run finished.",
                        payload={"status": "canceled"},
                        created_at=utc_now(),
                    )
                )
        return {
            "cancel_requested": True,
            "active": active is not None,
            "run": run_to_dict(run),
        }

    @app.get("/api/runs/{run_id}/diff")
    def run_diff(run_id: str) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return {"run": run_to_dict(run), "diff": run_diff_response(run.metadata)}

    @app.get("/api/runs/{run_id}/pr")
    def run_pr_artifact(run_id: str) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        artifact = build_pr_artifact(run)
        return {
            "run": run_to_dict(run),
            "pr_artifact": pr_artifact_to_dict(artifact),
        }

    @app.get("/api/runs/{run_id}/provenance")
    def run_provenance(run_id: str) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return _run_provenance_response(run)

    @app.post("/api/runs/{run_id}/replay")
    def replay_run(
        run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
            raw_request = _latest_raw_request_for_run(store, run)
            replay_payload = build_replay_request(
                run,
                raw_request=raw_request,
                reviewed_evidence=_reviewed_evidence_for_run(runtime_store, run.id),
            )
            if "stream" in payload:
                replay_payload["stream"] = bool(payload.get("stream"))
            if (
                replay_payload.get("execution_transport")
                == ExecutionTransport.NATIVE_STRUCTURED.value
            ):
                if durable_dispatcher is None:
                    raise ValueError(
                        "native_structured replay requires the durable runtime"
                    )
                if run.mode == "edit":
                    replay_payload["workspace_policy"] = "worktree"
                replay_session = runner.create_session(
                    title=f"Replay: {title_from_prompt(run.prompt)}",
                    workspace=run.workspace,
                    default_harness_id=run.harness_id,
                    default_model=run.model,
                    default_api_mode=run.api_mode,
                    default_mode=run.mode,
                )
                replay_extra = replay_payload.get("extra")
                replay_source_value = (
                    replay_extra.get("replay_source")
                    if isinstance(replay_extra, Mapping)
                    else None
                )
                replay_source = (
                    dict(replay_source_value)
                    if isinstance(replay_source_value, Mapping)
                    else {}
                )
                replay_session = store.update_session(
                    replay_session.id,
                    metadata={
                        **dict(replay_session.metadata),
                        "replay_source": replay_source,
                    },
                )
                submission = durable_dispatcher.submit(
                    replay_session.id,
                    replay_payload,
                    idempotency_key=f"replay:{run.id}:{replay_session.id}",
                    origin="manual",
                )
                return {
                    "session": session_to_dict(replay_session),
                    "run": run_to_dict(submission.queued.run),
                    "source_run": run_to_dict(run),
                    "replay_request": replay_payload,
                    "replay": {
                        "source": replay_source,
                        "destination_harness_session_id": replay_session.id,
                        "provider_session_pending": True,
                    },
                }
            result = runner.run_in_session(run.session_id, replay_payload)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response = result.to_dict()
        response["source_run"] = run_to_dict(run)
        response["replay_request"] = replay_payload
        return response

    @app.post("/api/runs/{run_id}/fork")
    def fork_run(
        run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
            validate_run_action_binding(run, payload)
            session = _fork_session_from_run(store, run)
            bundle = store.get_session_bundle(session.id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "source_run": run_to_dict(run),
            "session": _session_summary(store, session.id),
            "bundle": bundle_to_dict(bundle),
        }

    @app.get("/api/runs/{run_id}/patch")
    def run_patch(run_id: str) -> Response:
        try:
            run = store.get_run(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        artifact = build_pr_artifact(run)
        return Response(content=artifact.patch, media_type="text/plain")

    @app.post("/api/runs/{run_id}/apply", response_model=None)
    def apply_run_patch(
        run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any] | JSONResponse:
        try:
            run = store.get_run(run_id)
            branch_name = _optional_text(payload.get("branch_name"))
            review = review_run_diff(run.metadata, branch_name=branch_name)
            approval_response = _approval_gate(
                PermissionAction.GIT_APPLY,
                run,
                reason="Apply an isolated worktree diff to the source checkout.",
                preview=review.to_preview(),
                approval_binding=review.approval_binding,
                enforcement_owner=REVIEWED_PROMOTION_APPLY_OWNER,
            )
            if approval_response is not None:
                return approval_response
            workspace_execution = apply_run_diff(
                run.metadata,
                review=review,
                branch_name=branch_name,
            )
            metadata = {
                **dict(run.metadata),
                "workspace_execution": workspace_execution,
            }
            run = store.update_run(run.id, metadata=metadata)
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="worktree_applied",
                    message="Applied isolated worktree diff to the source checkout.",
                    payload={
                        "changed_files": workspace_execution.get("changed_files", []),
                        "applied_branch": workspace_execution.get("applied_branch"),
                        "source_sha": review.source_sha,
                        "patch_sha256": review.patch_sha256,
                    },
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "applied": True,
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
        }

    @app.post("/api/runs/{run_id}/branch", response_model=None)
    def create_run_branch(
        run_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any] | JSONResponse:
        try:
            run = store.get_run(run_id)
            branch_name = (
                _optional_text(payload.get("branch_name"))
                or build_pr_artifact(run).branch_name_suggestion
            )
            review = review_run_diff(run.metadata, branch_name=branch_name)
            approval_response = _approval_gate(
                PermissionAction.GIT_BRANCH_CREATE,
                run,
                reason="Create a local branch from the isolated run patch.",
                preview=review.to_preview(),
                approval_binding=review.approval_binding,
                enforcement_owner=REVIEWED_PROMOTION_BRANCH_OWNER,
            )
            if approval_response is not None:
                return approval_response
            branch = create_pr_branch(
                run,
                review=review,
                branch_name=branch_name,
            )
            metadata = {
                **dict(run.metadata),
                "workspace_execution": branch["workspace_execution"],
            }
            run = store.update_run(run.id, metadata=metadata)
            artifact = build_pr_artifact(run)
            metadata = {
                **dict(run.metadata),
                "pr_artifact": pr_artifact_to_dict(artifact),
            }
            run = store.update_run(run.id, metadata=metadata)
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="pr_branch_created",
                    message="Created local branch from run patch.",
                    payload={
                        "branch_name": branch["branch_name"],
                        "source_sha": review.source_sha,
                        "patch_sha256": review.patch_sha256,
                    },
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "branch_created": True,
            "branch_name": branch["branch_name"],
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
            "pr_artifact": pr_artifact_to_dict(build_pr_artifact(run)),
        }

    @app.post("/api/runs/{run_id}/discard")
    def discard_run_worktree_endpoint(run_id: str) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
            workspace_execution = discard_run_worktree(run.metadata)
            metadata = {
                **dict(run.metadata),
                "workspace_execution": workspace_execution,
            }
            run = store.update_run(run.id, metadata=metadata)
            store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=run.session_id,
                    run_id=run.id,
                    type="worktree_discarded",
                    message="Discarded isolated worktree for this run.",
                    payload={"worktree_path": workspace_execution.get("worktree_path")},
                    created_at=utc_now(),
                )
            )
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        except WorktreeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "discarded": True,
            "run": run_to_dict(run),
            "diff": run_diff_response(run.metadata),
        }

    @app.post("/api/runs/{run_id}/open-worktree")
    def open_run_worktree(run_id: str) -> dict[str, Any]:
        try:
            run = store.get_run(run_id)
            response = open_worktree_response(run.metadata)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc
        return {"run": run_to_dict(run), "worktree": response}

    @app.post("/api/sessions/run")
    def create_session_and_run(
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            result = session_service.create_and_run(payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/api/sessions/{session_id}/run")
    def run_in_session(
        session_id: str,
        payload: dict[str, Any] = Body(...),
    ) -> dict[str, Any]:
        try:
            result = session_service.run_turn(session_id, payload)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.to_dict()

    @app.get("/api/sessions/{session_id}/events")
    def session_events(
        session_id: str,
        run_id: str | None = Query(default=None),
        after_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        try:
            events = store.list_events(session_id, run_id=run_id, after_id=after_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "events": [
                {
                    "id": event.id,
                    "session_id": event.session_id,
                    "run_id": event.run_id,
                    "type": event.type,
                    "message": event.message,
                    "payload": dict(event.payload),
                    "created_at": event.created_at,
                }
                for event in events
            ]
        }

    @app.post("/api/run")
    def run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        harness_id = str(payload.get("harness_id") or "echo")
        try:
            harness = registry.get(harness_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        extra = dict(extra)
        if bool(payload.get("dry_run")):
            extra["dry_run"] = True
        try:
            api_mode = parse_api_mode(payload.get("api_mode"))
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid api_mode; expected v1 or v2",
            ) from exc
        try:
            capability = parse_capability(
                payload.get("capability") or HarnessCapability.CHAT_COMPLETIONS.value
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid capability",
            ) from exc
        try:
            builtin_tools = parse_builtin_tools(payload.get("builtin_tools"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if builtin_tools and api_mode is not GigaChatApiMode.V2:
            raise HTTPException(
                status_code=400,
                detail="built-in tools require /v2/chat/completions",
            )
        unsupported_builtin_tools = [
            tool.value
            for tool in builtin_tools
            if tool not in set(getattr(harness.spec(), "supported_builtin_tools", ()))
        ]
        if unsupported_builtin_tools:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{harness_id} does not support built-in tools: "
                    + ", ".join(unsupported_builtin_tools)
                ),
            )
        request = HarnessRequest(
            prompt=str(payload.get("prompt") or ""),
            model=_optional_text(payload.get("model")),
            api_mode=api_mode,
            capability=capability,
            mode=str(payload.get("mode") or "plan"),
            stream=bool(payload.get("stream")),
            workspace=resolve_workspace(_optional_text(payload.get("workspace"))),
            builtin_tools=builtin_tools,
            extra=extra,
        )
        preflight = build_preflight_report(
            prompt=request.prompt,
            workspace=request.workspace,
            data_dir=config.data_dir,
        )
        if preflight.hard_block:
            raise HTTPException(
                status_code=400,
                detail=format_preflight_block_message(preflight),
            )
        try:
            result = harness.run(request, config.to_context())
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail="Harness run failed",
            ) from exc
        return result_to_dict(result)

    app.include_router(create_catalog_router(services))
    app.include_router(create_projects_router(services))
    app.include_router(create_editor_router(services))
    app.include_router(create_project_memory_router(services))
    app.include_router(create_project_tools_router(services))
    app.include_router(create_evals_router(services))
    app.include_router(create_session_catalog_router(services))
    app.include_router(create_attachments_router(services))
    app.include_router(create_arena_router(services))
    app.include_router(create_native_sessions_router(services))
    app.include_router(
        create_native_process_start_router(
            services, native_login_broker=native_login_broker
        )
    )
    app.include_router(create_native_processes_router(services))
    app.include_router(agents_router)
    app.include_router(automation_router)
    app.include_router(approvals_router)
    app.include_router(cockpit_router)
    app.include_router(compatibility_router)
    app.include_router(evaluate_router)
    app.include_router(environments_router)
    app.include_router(integrations_router)
    app.include_router(tools_router)
    app.include_router(tui_actions_router)
    app.include_router(workbench_state_router)
    app.include_router(workbench_resources_router)
    app.include_router(workflows_router)
    app.include_router(runs_router)
    app.include_router(trace_replays_router)
    app.include_router(handoff_capsules_router)
    app.include_router(schedules_router)
    app.include_router(settings_router)
    app.include_router(create_file_preview_router(config.data_dir))
    app.include_router(create_provider_handoff_router(registry))
    # The shell catch-all must remain last so unknown API and asset paths never
    # become HTML responses.
    app.include_router(create_shell_router(services.ui_security))
    install_mutation_contracts(app)
    install_execution_contracts(app)
    if handler_errors := async_handler_contract_errors(app.routes):
        raise RuntimeError(
            "Harness async handler contract invalid: " + "; ".join(handler_errors)
        )
    return app


def validate_ui_bind(
    config: HarnessConfig,
    *,
    allow_remote: bool | None,
) -> None:
    """Admit remote binding only for the complete accepted OIDC profile."""
    if is_loopback_host(config.ui_host):
        return
    if allow_remote is False:
        raise ValueError(
            f"Refusing to bind GigaLoom UI to non-loopback host {config.ui_host}. "
            "Pass --allow-remote only with the complete single-issuer OIDC profile."
        )
    try:
        RemoteOIDCSettings.from_config(config)
    except RemoteIdentityError as exc:
        raise ValueError(
            f"Refusing to bind GigaLoom UI to non-loopback host {config.ui_host}. {exc}"
        ) from exc


def _fork_session_from_run(
    store: HarnessSessionStore,
    run: HarnessRun,
) -> HarnessSession:
    source = store.get_session(run.session_id)
    run_thread = run.metadata.get("app_server_thread")
    session_thread = source.metadata.get("app_server_thread")
    source_thread = (
        dict(run_thread)
        if isinstance(run_thread, Mapping)
        else dict(session_thread)
        if isinstance(session_thread, Mapping)
        else {}
    )
    metadata = {
        **dict(source.metadata),
        "forked_from_session_id": source.id,
        "forked_from_run_id": run.id,
    }
    metadata.pop("app_server_thread", None)
    metadata.pop("structured_session_link", None)
    metadata.pop("app_server_fork", None)
    if source_thread.get("thread_id"):
        metadata["app_server_fork"] = {
            "thread_id": source_thread["thread_id"],
            "turn_id": source_thread.get("latest_turn_id"),
            "source_session_id": source.id,
            "source_run_id": run.id,
        }
    fork = store.create_session(
        title=f"Fork: {source.title}",
        workspace=run.workspace or source.workspace,
        default_harness_id=run.harness_id,
        default_model=run.model,
        default_api_mode=run.api_mode,
        default_mode=run.mode,
        metadata=metadata,
    )
    for message in _messages_through_run(store.list_messages(source.id), run.id):
        store.append_message(
            replace(
                message,
                id=new_id("msg"),
                session_id=fork.id,
                run_id=None,
                created_at=utc_now(),
                metadata={
                    **dict(message.metadata),
                    "forked_from_message_id": message.id,
                    "forked_from_run_id": run.id,
                },
            )
        )
    return fork


def _messages_through_run(
    messages: tuple[HarnessMessage, ...],
    run_id: str,
) -> tuple[HarnessMessage, ...]:
    selected: list[HarnessMessage] = []
    seen_target_run = False
    for message in messages:
        selected.append(message)
        if message.run_id == run_id:
            seen_target_run = True
            if message.role in {"assistant", "error"}:
                break
        elif seen_target_run:
            selected.pop()
            break
    return tuple(selected)


async def _wait_for_started_run(
    *,
    store: HarnessSessionStore,
    session_id: str,
    before_run_ids: set[str],
    task: asyncio.Task[Any],
) -> HarnessRun:
    for _ in range(200):
        stored_runs = await run_in_threadpool(store.list_runs, session_id)
        runs = [run for run in stored_runs if run.id not in before_run_ids]
        if runs:
            return runs[-1]
        if task.done():
            task.result()
            break
        await asyncio.sleep(0.01)
    raise RuntimeError("Harness run did not start")
