"""Session orchestration for the Unified Harness chat cockpit."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import threading
import time
from typing import Any, Callable, Mapping

from gigaloom import proxy
from gigaloom.attachments import (
    AttachmentNotFoundError,
    FilesystemAttachmentStore,
    HarnessAttachment,
    attachment_to_dict,
)
from gigaloom.config import HarnessConfig
from gigaloom.execution import ExecutionTransport
from gigaloom.execution.admission import RunAdmissionService
from gigaloom.execution.attachments import (
    PreparedAttachments,
    message_attachment_metadata,
    run_attachment_metadata,
)
from gigaloom.execution.continuation import (
    build_continuation_plan,
)
from gigaloom.execution.finalization import RunFinalizationService
from gigaloom.execution.invocation import (
    HarnessExecutionService,
    InvocationAccumulator,
    cancel_requested,
)
from gigaloom.execution.milestones import PersistenceMilestone
from gigaloom.execution.options import RunOptions
from gigaloom.execution.persistence import RunPersistenceService
from gigaloom.execution.preparation import RunPreparationService
from gigaloom.execution.trust_context import ExecutionTrustTracker
from gigaloom.managed_mcp import HeadlessManagedMCPSnapshotStore
from gigaloom.mcp import build_mcp_inventory
from gigaloom.native.models import parse_invocation_mode
from gigaloom.project import load_project_config, resolve_project
from gigaloom.project_memory import (
    FilesystemProjectMemoryStore,
    ProjectMemoryEntry,
    memory_entries_to_context,
    memory_entries_to_prompt,
)
from gigaloom.preflight import (
    PreflightBlockedError,
    build_preflight_report,
    preflight_report_to_dict,
)
from gigaloom.permission_simulator import (
    build_permission_simulation,
    extension_permission_contract,
)
from gigaloom.pr_artifacts import build_pr_artifact, pr_artifact_to_dict
from gigaloom.provider_account_sessions import (
    PROVIDER_ACCOUNT_BINDING_KEY,
    ProviderAccountBindingProvider,
    prepare_provider_account_binding,
)
from gigaloom.provenance import (
    build_run_provenance,
    run_provenance_to_dict,
)
from gigaloom.readiness import build_execution_readiness
from gigaloom.registry import HarnessRegistry
from gigaloom.runtime.structured import (
    DurableStructuredHarness,
    requested_execution_transport,
)
from gigaloom.runtime.policy import PermissionAction, permission_profile
from gigaloom.sessions.conversation import (
    edited_message_metadata,
)
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessSession,
    HarnessSessionBundle,
    HarnessStoredEvent,
    bundle_to_dict,
    run_to_dict,
    session_to_dict,
)
from gigaloom.sessions.store import (
    HarnessSessionStore,
    SessionNotFoundError,
    new_id,
    title_from_prompt,
    utc_now,
)
from gigaloom.session_titles import (
    SessionTitleGeneration,
    apply_provider_native_title,
    claim_fallback_title,
    complete_fallback_title,
    title_diagnostics,
)
from gigaloom.types import (
    GigaChatApiMode,
    HarnessChatMessage,
    HarnessEvent,
    HarnessEventType,
    HeadlessContinuationStrategy,
    HarnessRequest,
    HarnessResult,
    event_to_dict,
    parse_api_mode,
    parse_builtin_tools,
    parse_capability,
    result_to_dict,
)
from gigaloom.worktrees import (
    capture_workspace_diff,
    parse_workspace_policy,
)
from gigaloom.workspace import resolve_workspace

MAX_HISTORY_MESSAGES = 20


@dataclass(frozen=True)
class HarnessSessionRunResult:
    """Lightweight run result with an explicit legacy bundle adapter."""

    session: HarnessSession
    run: HarnessRun
    result: HarnessResult
    _bundle_loader: Callable[[], HarnessSessionBundle] = field(
        repr=False,
        compare=False,
    )
    _bundle: HarnessSessionBundle | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @property
    def bundle(self) -> HarnessSessionBundle:
        """Materialize the complete bundle for explicit legacy/export callers."""
        if self._bundle is None:
            object.__setattr__(self, "_bundle", self._bundle_loader())
        bundle = self._bundle
        if bundle is None:  # pragma: no cover - frozen assignment is deterministic
            raise RuntimeError("session bundle materialization failed")
        return bundle

    @property
    def has_materialized_bundle(self) -> bool:
        """Return whether a caller explicitly requested the complete bundle."""
        return self._bundle is not None

    def to_lightweight_dict(self) -> dict[str, Any]:
        """Serialize session summary, run, and harness result without history."""
        payload = {
            "session": session_to_dict(self.session),
            "run": run_to_dict(self.run),
            "result": result_to_dict(self.result),
        }
        self._add_attachment_metadata(payload)
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Serialize the legacy synchronous response with an explicit full export."""
        payload = bundle_to_dict(self.bundle)
        payload.update(
            {
                "session": payload["session"],
                "run": run_to_dict(self.run),
                "result": result_to_dict(self.result),
            }
        )
        self._add_attachment_metadata(payload)
        return payload

    def _add_attachment_metadata(self, payload: dict[str, Any]) -> None:
        attachments = self.run.metadata.get("attachments")
        if attachments:
            payload["attachments"] = attachments
        attachment_render_plan = self.run.metadata.get("attachment_render_plan")
        if attachment_render_plan:
            payload["attachment_render_plan"] = attachment_render_plan


@dataclass(frozen=True)
class QueuedHarnessRun:
    """Prepared durable run and its single logical user message."""

    session: HarnessSession
    run: HarnessRun
    user_message: HarnessMessage


class HarnessSessionRunner:
    """Create and run normalized harness sessions."""

    def __init__(
        self,
        *,
        registry: HarnessRegistry,
        config: HarnessConfig,
        store: HarnessSessionStore,
        attachment_store: FilesystemAttachmentStore | None = None,
        memory_store: FilesystemProjectMemoryStore | None = None,
        provider_account_provider: ProviderAccountBindingProvider | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.store = store
        self.attachment_store = attachment_store or FilesystemAttachmentStore(
            config.data_dir
        )
        self.memory_store = memory_store or FilesystemProjectMemoryStore()
        self.provider_account_provider = provider_account_provider
        self.admission_service = RunAdmissionService()
        self.preparation_service = RunPreparationService()
        self.invocation_service = HarnessExecutionService()
        self.persistence_service = RunPersistenceService(
            store=store,
            id_factory=new_id,
            clock=utc_now,
        )
        self.finalization_service = RunFinalizationService()

    def preflight(
        self,
        payload: Mapping[str, Any],
        *,
        session_id: str | None = None,
        durable: bool = False,
    ):
        """Build a pre-run safety report without invoking a harness."""
        session = self.store.get_session(session_id) if session_id is not None else None
        options = self._run_options(payload, session=session)
        if session is not None:
            prepare_provider_account_binding(
                session,
                provider_id=str(options["harness_id"]),
                native_session_id=_optional_text(options.get("native_session_id")),
                provider=self.provider_account_provider,
            )
        previous_messages = ()
        if session is not None and not bool(
            _mapping(options["extra"]).get("isolated_history")
        ):
            previous_messages = self.preparation_service.previous_messages(
                self.store,
                session.id,
                edit_message_id=_edit_message_id(options),
                current_user_message_id=None,
                limit=MAX_HISTORY_MESSAGES,
            )
        if options["attachment_ids"] and session is None:
            raise ValueError("session_id is required for attachment preflight")
        attachments = (
            self._load_attachments(session.id, options["attachment_ids"])
            if session is not None
            else ()
        )
        project_memory = self._load_project_memory(options["workspace"])
        permission_simulation = self._permission_simulation(options)
        return build_preflight_report(
            prompt=options["prompt"],
            workspace=options["workspace"],
            previous_messages=previous_messages,
            attachments=attachments,
            project_memory=project_memory,
            data_dir=self.config.data_dir,
            max_history_messages=MAX_HISTORY_MESSAGES,
            readiness=self._execution_readiness(options, durable=durable),
            permission_simulation=permission_simulation,
        )

    def create_session(
        self,
        *,
        title: str | None = None,
        workspace: str | None = None,
        default_harness_id: str = "echo",
        default_model: str | None = None,
        default_api_mode: GigaChatApiMode | str | None = None,
        default_mode: str = "plan",
        metadata: Mapping[str, Any] | None = None,
    ) -> HarnessSession:
        """Create a new empty session."""
        resolved_workspace = resolve_workspace(workspace)
        return self.store.create_session(
            title=title,
            workspace=resolved_workspace,
            default_harness_id=default_harness_id,
            default_model=default_model,
            default_api_mode=parse_api_mode(default_api_mode),
            default_mode=default_mode,
            metadata={
                **dict(metadata or {}),
                **_project_metadata(
                    resolved_workspace,
                    data_dir=self.config.data_dir,
                ),
            },
        )

    def create_and_run(
        self,
        payload: Mapping[str, Any],
        *,
        cancel_event: Any | None = None,
    ) -> HarnessSessionRunResult:
        """Create a session from a prompt and immediately run it."""
        options = self._run_options(payload, session=None)
        session = self.create_session(
            title=_optional_text(payload.get("title")),
            workspace=options["workspace"],
            default_harness_id=options["harness_id"],
            default_model=options["model"],
            default_api_mode=options["api_mode"],
            default_mode=options["mode"],
        )
        return self.run_in_session(session.id, payload, cancel_event=cancel_event)

    def enqueue_in_session(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        run_id: str,
    ) -> QueuedHarnessRun:
        """Prepare one durable headless run without executing its harness."""
        session = self.store.get_session(session_id)
        options = self._run_options(payload, session=session)
        session, provider_account_binding = self._prepare_provider_account_session(
            session,
            options,
        )
        if (
            options["invocation_mode"].value != "headless"
            and options["execution_transport"]
            is not ExecutionTransport.NATIVE_STRUCTURED
        ):
            raise ValueError(
                "durable jobs exclude native terminal execution without a proven "
                "native_structured transport"
            )
        report = self.preflight(payload, session_id=session_id, durable=True)
        if report.hard_block:
            raise PreflightBlockedError(report)
        attachments = self._load_attachments(
            session.id,
            options["attachment_ids"],
        )
        prepared_attachments = PreparedAttachments(
            attachments=attachments,
            metadata=tuple(
                run_attachment_metadata(attachment) for attachment in attachments
            ),
        )
        attachment_payloads = prepared_attachments.metadata
        managed_mcp_snapshot = self._prepare_managed_mcp_snapshot(options)
        trust_tracker = ExecutionTrustTracker.from_execution_options(
            options, attachments
        )
        _validate_continuation_identity(session, options)
        message_id = new_id("msg")
        run = self.store.create_run(
            run_id=run_id,
            session_id=session.id,
            harness_id=options["harness_id"],
            status="queued",
            prompt=options["prompt"],
            model=options["model"],
            api_mode=options["api_mode"],
            capability=options["capability"],
            mode=options["mode"],
            workspace=options["workspace"],
            invocation_mode=options["invocation_mode"],
            metadata={
                "invocation_mode": options["invocation_mode"].value,
                "execution_transport": (
                    options["execution_transport"].value
                    if options["execution_transport"] is not None
                    else None
                ),
                "preflight": preflight_report_to_dict(report),
                "durable": True,
                **message_attachment_metadata(attachment_payloads),
                **(
                    {"managed_mcp_snapshot": managed_mcp_snapshot}
                    if managed_mcp_snapshot is not None
                    else {}
                ),
                "trust_context": trust_tracker.snapshot().to_dict(),
                **edited_message_metadata(_edit_message_id(options)),
                **_agent_metadata(options),
                **_workbench_admission_metadata(options),
                **(
                    {PROVIDER_ACCOUNT_BINDING_KEY: provider_account_binding}
                    if provider_account_binding is not None
                    else {}
                ),
            },
        )
        user_message = self.store.append_message(
            HarnessMessage(
                id=message_id,
                session_id=session.id,
                run_id=run.id,
                role="user",
                content=options["prompt"],
                created_at=utc_now(),
                harness_id=options["harness_id"],
                model=options["model"],
                api_mode=options["api_mode"],
                metadata={
                    **message_attachment_metadata(attachment_payloads),
                    **edited_message_metadata(_edit_message_id(options)),
                },
            )
        )
        updated_session = self.store.update_session(
            session.id,
            default_harness_id=options["harness_id"],
            default_model=options["model"],
            default_api_mode=options["api_mode"],
            default_mode=options["mode"],
            workspace=options["workspace"],
            metadata={
                **dict(session.metadata),
                **_workbench_session_selection_metadata(options),
            },
        )
        self._schedule_session_title(session, run.id, options)
        return QueuedHarnessRun(
            session=updated_session, run=run, user_message=user_message
        )

    def run_in_session(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        cancel_event: Any | None = None,
        existing_run_id: str | None = None,
        user_message_id: str | None = None,
        excluded_history_run_ids: tuple[str, ...] = (),
        runtime_metadata: Mapping[str, Any] | None = None,
        process_sink: Any | None = None,
        durable: bool = False,
    ) -> HarnessSessionRunResult:
        """Run one prompt inside an existing session."""
        execution_context = self.admission_service.admit(
            self,
            session_id,
            payload,
            user_message_id=user_message_id,
            excluded_history_run_ids=excluded_history_run_ids,
            new_message_id=new_id,
            history_resolver=self.preparation_service.previous_messages,
            history_limit=MAX_HISTORY_MESSAGES,
            edit_message_id=_edit_message_id,
        )
        session = execution_context.session
        options = execution_context.options
        harness = execution_context.harness
        logical_user_message_id = execution_context.logical_user_message_id
        provider_account_binding = execution_context.provider_account_binding
        prepared_attachments = self.preparation_service.prepare_attachments(
            self,
            execution_context,
            metadata_factory=run_attachment_metadata,
        )
        attachments = prepared_attachments.attachments
        attachment_payloads = prepared_attachments.metadata
        attachment_render_plan_payload = prepared_attachments.render_plan_payload
        project_memory = self._load_project_memory(options["workspace"])
        project_memory_payload = (
            memory_entries_to_context(project_memory) if project_memory else None
        )
        readiness: Mapping[str, Any] | None = None
        if durable and existing_run_id is not None:
            candidate_run_ids = (
                existing_run_id,
                *reversed(excluded_history_run_ids),
            )
            for candidate_run_id in candidate_run_ids:
                try:
                    queued_run = self.store.get_run(candidate_run_id)
                except KeyError:
                    continue
                if queued_run.session_id != session.id:
                    continue
                queued_preflight = _mapping(queued_run.metadata).get("preflight")
                if not isinstance(queued_preflight, Mapping):
                    continue
                queued_readiness = queued_preflight.get("readiness")
                if isinstance(queued_readiness, Mapping):
                    # Durable submission already performed admission checks before
                    # creating the first queued run. Retries reuse the last retained
                    # attempt's immutable evidence before their new run exists.
                    readiness = dict(queued_readiness)
                    break
        if readiness is None:
            readiness = self._execution_readiness(options, durable=durable)
        permission_simulation = self._permission_simulation(options)
        preflight = build_preflight_report(
            prompt=options["prompt"],
            workspace=options["workspace"],
            previous_messages=execution_context.previous_messages,
            attachments=attachments,
            project_memory=project_memory,
            data_dir=self.config.data_dir,
            max_history_messages=MAX_HISTORY_MESSAGES,
            readiness=readiness,
            permission_simulation=permission_simulation,
        )
        if preflight.hard_block:
            raise PreflightBlockedError(preflight)
        managed_mcp_snapshot = self._prepare_managed_mcp_snapshot(options)
        trust_tracker = ExecutionTrustTracker.from_execution_options(
            options, attachments
        )
        self.admission_service.validate_continuation_identity(execution_context)
        preflight_payload = preflight_report_to_dict(preflight)
        effective_prompt = _prompt_with_project_memory(
            options["prompt"],
            project_memory,
        )
        run_metadata: dict[str, Any] = {
            "invocation_mode": options["invocation_mode"].value,
            "execution_transport": (
                options["execution_transport"].value
                if options["execution_transport"] is not None
                else None
            ),
            "native_resume": _native_resume_metadata(options["harness_id"]),
            "preflight": preflight_payload,
            **(
                {"managed_mcp_snapshot": managed_mcp_snapshot}
                if managed_mcp_snapshot is not None
                else {}
            ),
            "trust_context": trust_tracker.snapshot().to_dict(),
            **_agent_metadata(options),
            **_workbench_admission_metadata(options),
            **(
                {PROVIDER_ACCOUNT_BINDING_KEY: provider_account_binding}
                if provider_account_binding is not None
                else {}
            ),
        }
        edit_message_id = _edit_message_id(options)
        if edit_message_id is not None:
            run_metadata["edited_from_message_id"] = edit_message_id
        if options["builtin_tools"]:
            run_metadata["builtin_tools"] = [
                tool.value for tool in options["builtin_tools"]
            ]
        if runtime_metadata:
            run_metadata["runtime"] = dict(runtime_metadata)
        if project_memory_payload:
            run_metadata["project_memory"] = project_memory_payload
        if attachment_payloads:
            run_metadata["attachment_ids"] = list(options["attachment_ids"])
            run_metadata["attachments"] = list(attachment_payloads)
        if attachment_render_plan_payload:
            run_metadata["attachment_render_plan"] = attachment_render_plan_payload
        if existing_run_id is not None:
            try:
                run = self.store.get_run(existing_run_id)
            except KeyError:
                run = self.store.create_run(
                    run_id=existing_run_id,
                    session_id=session.id,
                    harness_id=options["harness_id"],
                    status="running",
                    prompt=options["prompt"],
                    model=options["model"],
                    api_mode=options["api_mode"],
                    capability=options["capability"],
                    mode=options["mode"],
                    workspace=options["workspace"],
                    invocation_mode=options["invocation_mode"],
                    started_at=utc_now(),
                    metadata=run_metadata,
                )
            else:
                run = self.store.update_run(
                    run.id,
                    status="running",
                    started_at=run.started_at or utc_now(),
                    metadata={**dict(run.metadata), **run_metadata},
                )
        else:
            run = self.store.create_run(
                session_id=session.id,
                harness_id=options["harness_id"],
                status="running",
                prompt=options["prompt"],
                model=options["model"],
                api_mode=options["api_mode"],
                capability=options["capability"],
                mode=options["mode"],
                workspace=options["workspace"],
                invocation_mode=options["invocation_mode"],
                started_at=utc_now(),
                metadata=run_metadata,
            )
        workspace_execution = self.preparation_service.prepare_workspace(
            execution_context,
            run_id=run.id,
            data_dir=self.config.data_dir,
        )
        run_metadata["workspace_execution"] = workspace_execution.to_metadata()
        user_messages = (
            (
                HarnessMessage(
                    id=logical_user_message_id,
                    session_id=session.id,
                    run_id=run.id,
                    role="user",
                    content=options["prompt"],
                    created_at=utc_now(),
                    harness_id=options["harness_id"],
                    model=options["model"],
                    api_mode=options["api_mode"],
                    metadata={
                        **message_attachment_metadata(attachment_payloads),
                        **edited_message_metadata(edit_message_id),
                    },
                ),
            )
            if user_message_id is None
            else ()
        )
        run_started_event = self.persistence_service.event(
            session.id,
            run.id,
            HarnessEventType.RUN_STARTED.value,
            "Harness run started.",
            {
                "harness_id": options["harness_id"],
                "model": options["model"],
                "api_mode": options["api_mode"].value,
                "mode": options["mode"],
                "invocation_mode": options["invocation_mode"].value,
                "workspace_policy": workspace_execution.policy.value,
                "requested_workspace_policy": workspace_execution.requested_policy.value,
                "attachment_count": len(attachment_payloads),
                "builtin_tools": [tool.value for tool in options["builtin_tools"]],
            },
        )
        started = self.persistence_service.persist_milestone(
            PersistenceMilestone.RUN_STARTED,
            session_id=session.id,
            run_id=run.id,
            run_patch={"metadata": run_metadata},
            messages=user_messages,
            events=(run_started_event,),
        )
        run = started.runs[-1]
        if existing_run_id is None:
            self._schedule_session_title(session, run.id, options)
        if workspace_execution.fallback_reason:
            self._append_event(
                session.id,
                run.id,
                HarnessEventType.WARNING.value,
                "Workspace execution policy fell back to current workspace.",
                {
                    "requested_policy": workspace_execution.requested_policy.value,
                    "fallback_reason": workspace_execution.fallback_reason,
                },
            )
        request_messages = self._build_request_messages(
            execution_context.previous_messages,
            prompt=effective_prompt,
        )
        request_extra = _request_extra(
            options["extra"],
            attachment_payloads,
            attachment_render_plan_payload,
        )
        request_extra["workspace_execution"] = workspace_execution.to_metadata()
        if runtime_metadata:
            request_extra["runtime"] = dict(runtime_metadata)
        if project_memory_payload:
            request_extra["project_memory"] = project_memory_payload
        request_extra["preflight"] = preflight_payload
        # Provider invocation receives only the labels known before execution.
        request_extra["trust_context"] = trust_tracker.snapshot().to_dict()
        invocation = InvocationAccumulator(source_observer=trust_tracker.observe_event)

        def append_streamed_event(event: HarnessEvent) -> None:
            self._append_event(
                session.id,
                run.id,
                event.type,
                event.message,
                event_to_dict(event)["payload"],
            )

        request = HarnessRequest(
            prompt=effective_prompt,
            model=options["model"],
            api_mode=options["api_mode"],
            capability=options["capability"],
            mode=options["mode"],
            invocation_mode=options["invocation_mode"],
            execution_transport=options["execution_transport"],
            stream=options["stream"],
            workspace=workspace_execution.request_workspace,
            messages=request_messages,
            attachments=tuple(
                attachment_to_dict(attachment)
                for attachment in prepared_attachments.attachments
            ),
            attachment_render_plan=attachment_render_plan_payload,
            builtin_tools=options["builtin_tools"],
            session_id=session.id,
            run_id=run.id,
            native_session_id=options["native_session_id"],
            cancel_event=cancel_event,
            event_sink=invocation.event_sink(append_streamed_event),
            process_sink=process_sink,
            extra=request_extra,
        )
        continuation = build_continuation_plan(
            request,
            harness=execution_context.harness,
            session=execution_context.session,
            previous_messages=execution_context.previous_messages,
            prompt_id=execution_context.logical_user_message_id,
            edit_source=_edit_continuation_source(
                self.store,
                edit_message_id=edit_message_id,
                previous_messages=execution_context.previous_messages,
            ),
        )
        request_extra["continuation"] = continuation.to_dict()
        request = replace(request, extra=request_extra)
        run_metadata["continuation"] = continuation.public_payload()
        run = self.store.update_run(run.id, metadata=run_metadata)
        if execution_context.previous_messages and continuation.get("strategy") in {
            HeadlessContinuationStrategy.UNSUPPORTED.value,
            HeadlessContinuationStrategy.DEGRADED_REPLAY.value,
            HeadlessContinuationStrategy.ONE_SHOT.value,
        }:
            self._append_event(
                session.id,
                run.id,
                HarnessEventType.WARNING.value,
                str(continuation.get("reason") or "Headless continuity is degraded."),
                {
                    "strategy": continuation.get("strategy"),
                    "continuity_proven": False,
                },
            )
        raw_request = {
            "harness_id": options["harness_id"],
            "prompt": effective_prompt,
            "model": options["model"],
            "api_mode": options["api_mode"].value,
            "capability": options["capability"].value,
            "mode": options["mode"],
            "invocation_mode": options["invocation_mode"].value,
            "execution_transport": (
                options["execution_transport"].value
                if options["execution_transport"] is not None
                else None
            ),
            "stream": options["stream"],
            "workspace": options["workspace"],
            "effective_workspace": workspace_execution.request_workspace,
            "workspace_policy": workspace_execution.policy.value,
            "requested_workspace_policy": workspace_execution.requested_policy.value,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request_messages
            ],
            "builtin_tools": [tool.value for tool in options["builtin_tools"]],
            "extra": options["extra"],
            "continuation": continuation.public_payload(),
        }
        if effective_prompt != options["prompt"]:
            raw_request["original_prompt"] = options["prompt"]
        if project_memory_payload:
            raw_request["project_memory"] = project_memory_payload
        if attachment_payloads:
            raw_request["attachment_ids"] = list(options["attachment_ids"])
            raw_request["attachments"] = list(attachment_payloads)
        if attachment_render_plan_payload:
            raw_request["attachment_render_plan"] = attachment_render_plan_payload
        raw_request["preflight"] = preflight_payload
        raw_request_record = self.persistence_service.append_raw_request(
            session_id=session.id,
            run_id=run.id,
            payload=raw_request,
        )
        self._append_event(
            session.id,
            run.id,
            HarnessEventType.RAW_REQUEST.value,
            "Stored redacted harness request.",
            {
                "message_count": len(request_messages),
                "attachment_count": len(attachment_payloads),
                "memory_count": len(project_memory),
                "preflight_finding_count": len(preflight.findings),
            },
        )
        if preflight.findings:
            self._append_event(
                session.id,
                run.id,
                HarnessEventType.WARNING.value,
                "Preflight completed with warnings.",
                {
                    "max_severity": preflight.max_severity,
                    "finding_count": len(preflight.findings),
                    "codes": sorted({finding.code for finding in preflight.findings}),
                },
            )
        result = self.invocation_service.invoke(
            harness=harness,
            request=request,
            config_context=self.config.to_context(),
            durable=durable,
            structured_harness_type=DurableStructuredHarness,
            cancel_event=cancel_event,
        )

        raw_response_record = self.persistence_service.append_raw_response(
            session_id=session.id,
            run_id=run.id,
            payload=result_to_dict(result),
        )
        self._append_event(
            session.id,
            run.id,
            HarnessEventType.RAW_RESPONSE.value,
            "Stored redacted harness response.",
            {"ok": result.ok},
        )
        for event in result.events:
            if invocation.was_emitted(event):
                continue
            self._append_event(
                session.id,
                run.id,
                event.type,
                event.message,
                event_to_dict(event)["payload"],
            )
            invocation.observe(event)
        if result.ok:
            trust_tracker.observe_generated_output(result.text)

        latest_usage = invocation.latest_usage
        terminal = self.finalization_service.resolve(
            result,
            canceled=cancel_requested(cancel_event),
            latest_usage=latest_usage,
            reasoning=invocation.reasoning(),
        )
        status = terminal.status
        role = terminal.role
        content = terminal.content
        error = terminal.error
        terminal_message = HarnessMessage(
            id=new_id("msg"),
            session_id=session.id,
            run_id=run.id,
            role=role,
            content=content,
            created_at=utc_now(),
            harness_id=options["harness_id"],
            model=options["model"],
            api_mode=options["api_mode"],
            metadata=terminal.message_metadata,
        )
        terminal_event = self.persistence_service.event(
            session.id,
            run.id,
            terminal.event_type,
            terminal.event_message,
            {"role": role},
        )
        terminal_events = [terminal_event]
        metadata = dict(run_metadata)
        # The terminal run record receives the final observed influence snapshot.
        metadata["trust_context"] = trust_tracker.snapshot().to_dict()
        app_server_thread = _mapping(result.raw).get("app_server_thread")
        structured_session_link = _mapping(result.raw).get("structured_session_link")
        if isinstance(app_server_thread, Mapping) and app_server_thread:
            metadata["app_server_thread"] = dict(app_server_thread)
        if isinstance(structured_session_link, Mapping) and structured_session_link:
            metadata["structured_session_link"] = dict(structured_session_link)
        if latest_usage:
            metadata["usage"] = dict(latest_usage)
        if options["mode"] == "edit":
            workspace_diff = capture_workspace_diff(workspace_execution)
            if workspace_diff is not None:
                workspace_metadata = {
                    **dict(metadata.get("workspace_execution", {})),
                    **workspace_diff.to_metadata(),
                }
                metadata["workspace_execution"] = workspace_metadata
                metadata["diff"] = workspace_diff.patch
                metadata["diff_captured"] = workspace_diff.captured
                if workspace_diff.captured:
                    terminal_events.append(
                        self.persistence_service.event(
                            session.id,
                            run.id,
                            HarnessEventType.FILE_CHANGED.value,
                            "Captured workspace diff.",
                            {
                                "changed_files": list(workspace_diff.changed_files),
                                "untracked_files": list(workspace_diff.untracked_files),
                                "workspace_policy": workspace_execution.policy.value,
                            },
                        )
                    )
        pr_artifact_run = HarnessRun(
            id=run.id,
            session_id=session.id,
            harness_id=options["harness_id"],
            status=status,
            prompt=options["prompt"],
            model=options["model"],
            api_mode=options["api_mode"],
            capability=options["capability"],
            mode=options["mode"],
            workspace=options["workspace"],
            created_at=run.created_at,
            updated_at=run.updated_at,
            invocation_mode=options["invocation_mode"],
            started_at=run.started_at,
            finished_at=utc_now(),
            error=error,
            command=result.command,
            native_session_id=run.native_session_id,
            metadata=metadata,
        )
        metadata["pr_artifact"] = pr_artifact_to_dict(
            build_pr_artifact(
                pr_artifact_run,
                result_text=content if role == "assistant" else None,
                result_raw=result.raw,
            )
        )
        terminal_result = self.persistence_service.persist_milestone(
            PersistenceMilestone.RUN_TERMINAL,
            session_id=session.id,
            run_id=run.id,
            run_patch={
                "status": status,
                "finished_at": utc_now(),
                "error": error,
                "command": result.command,
                "metadata": metadata,
            },
            messages=(terminal_message,),
            events=tuple(terminal_events),
        )
        updated_run = terminal_result.runs[-1]
        session_patch: dict[str, Any] = {
            "default_harness_id": options["harness_id"],
            "default_model": options["model"],
            "default_api_mode": options["api_mode"],
            "default_mode": options["mode"],
            "workspace": options["workspace"],
        }
        project_metadata = _project_metadata(
            options["workspace"],
            data_dir=self.config.data_dir,
        )
        latest_session = self.store.get_session(session.id)
        session_metadata = {**latest_session.metadata, **project_metadata}
        session_metadata.update(_workbench_session_selection_metadata(options))
        if isinstance(app_server_thread, Mapping) and app_server_thread:
            session_metadata["app_server_thread"] = dict(app_server_thread)
            session_metadata.pop("app_server_fork", None)
        if isinstance(structured_session_link, Mapping) and structured_session_link:
            session_metadata["structured_session_link"] = dict(structured_session_link)
        if session_metadata:
            session_patch["metadata"] = session_metadata
        updated_session = self.store.update_session(session.id, **session_patch)
        native_title = _provider_native_title(result)
        if native_title is not None:
            native_updated = apply_provider_native_title(
                self.store,
                session.id,
                title=native_title[0],
                run_id=run.id,
                provider=options["harness_id"],
                source_id=native_title[1],
            )
            if native_updated is not None:
                self._append_title_event(native_updated, run.id)
                updated_session = native_updated
        run_finished_event = self.persistence_service.event(
            session.id,
            run.id,
            HarnessEventType.RUN_FINISHED.value,
            "Harness run finished.",
            {"status": status},
        )
        provenance = build_run_provenance(
            updated_run,
            session=updated_session,
            spec=harness.spec(),
            raw_requests=(raw_request_record,),
            raw_responses=(raw_response_record,),
            events=(
                *self.persistence_service.provenance_events(run.id),
                run_finished_event,
            ),
            data_dir=self.config.data_dir,
        )
        metadata = {
            **dict(updated_run.metadata),
            "provenance": run_provenance_to_dict(provenance),
        }
        provenance_result = self.persistence_service.persist_milestone(
            PersistenceMilestone.PROVENANCE_STORED,
            session_id=session.id,
            run_id=run.id,
            run_patch={"metadata": metadata},
            events=(run_finished_event,),
        )
        updated_run = provenance_result.runs[-1]
        return HarnessSessionRunResult(
            session=updated_session,
            run=updated_run,
            result=result,
            _bundle_loader=lambda: self._export_session_bundle(session.id),
        )

    def _run_options(
        self,
        payload: Mapping[str, Any],
        *,
        session: HarnessSession | None,
    ) -> RunOptions:
        prompt = str(payload.get("prompt") or "")
        harness_id = str(
            payload.get("harness_id")
            or (session.default_harness_id if session else "echo")
        )
        spec = self.registry.get(harness_id).spec()
        model = _optional_text(payload.get("model"))
        if model is None and session is not None:
            model = session.default_model
        if model is None:
            model = self.config.default_model
        api_mode = parse_api_mode(
            payload.get("api_mode")
            or (session.default_api_mode if session else self.config.default_api_mode)
        )
        builtin_tools = parse_builtin_tools(payload.get("builtin_tools"))
        if builtin_tools and api_mode is not GigaChatApiMode.V2:
            raise ValueError("built-in tools require /v2/chat/completions")
        supported_builtin_tools = set(getattr(spec, "supported_builtin_tools", ()))
        unsupported_builtin_tools = [
            tool.value for tool in builtin_tools if tool not in supported_builtin_tools
        ]
        if unsupported_builtin_tools:
            raise ValueError(
                f"{harness_id} does not support built-in tools: "
                + ", ".join(unsupported_builtin_tools)
            )
        capability = parse_capability(
            payload.get("capability")
            or (spec.capabilities[0].value if spec.capabilities else None)
        )
        mode = str(payload.get("mode") or (session.default_mode if session else "plan"))
        invocation_mode = parse_invocation_mode(payload.get("invocation_mode"))
        execution_transport = requested_execution_transport(payload)
        workspace = _optional_text(payload.get("workspace"))
        if workspace is None and session is not None:
            workspace = session.workspace
        workspace = resolve_workspace(workspace)
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        extra = dict(extra)
        if bool(payload.get("dry_run")):
            extra["dry_run"] = True
        if "continue_native" in payload:
            extra["continue_native"] = bool(payload.get("continue_native"))
        attachment_ids = _attachment_ids(payload.get("attachment_ids"))
        workspace_policy = parse_workspace_policy(
            payload.get("workspace_policy") or extra.get("workspace_policy")
        )
        origin = str(extra.get("permission_origin") or "interactive")
        selected_permission_profile = permission_profile(
            payload.get("permission_profile"),
            origin=origin,
        )
        required_permission_actions = _permission_actions(
            extra.get("required_permission_actions")
        )
        return RunOptions(
            prompt=prompt,
            harness_id=harness_id,
            harness_kind=spec.kind,
            model=model,
            api_mode=api_mode,
            builtin_tools=builtin_tools,
            capability=capability,
            mode=mode,
            invocation_mode=invocation_mode,
            execution_transport=execution_transport,
            workspace=workspace,
            stream=bool(payload.get("stream")),
            extra=extra,
            native_session_id=_optional_text(payload.get("native_session_id")),
            attachment_ids=attachment_ids,
            workspace_policy=workspace_policy,
            permission_profile=selected_permission_profile.id,
            permission_origin=origin,
            required_permission_actions=required_permission_actions,
            agent_id=_optional_text(payload.get("agent_id")),
            agent_profile_snapshot=(
                dict(payload["agent_profile_snapshot"])
                if isinstance(payload.get("agent_profile_snapshot"), Mapping)
                else None
            ),
            agent_execution_plan=(
                dict(payload["agent_execution_plan"])
                if isinstance(payload.get("agent_execution_plan"), Mapping)
                else None
            ),
        )

    def _prepare_provider_account_session(
        self,
        session: HarnessSession,
        options: Mapping[str, Any],
    ) -> tuple[HarnessSession, dict[str, Any] | None]:
        binding = prepare_provider_account_binding(
            session,
            provider_id=str(options["harness_id"]),
            native_session_id=_optional_text(options.get("native_session_id")),
            provider=self.provider_account_provider,
        )
        if binding is None or session.metadata.get(PROVIDER_ACCOUNT_BINDING_KEY):
            return session, binding
        updated = self.store.update_session(
            session.id,
            metadata={
                **dict(session.metadata),
                PROVIDER_ACCOUNT_BINDING_KEY: binding,
            },
        )
        return updated, binding

    def _build_request_messages(
        self,
        previous_messages: tuple[HarnessMessage, ...],
        *,
        prompt: str,
    ) -> tuple[HarnessChatMessage, ...]:
        history = [
            HarnessChatMessage(role=message.role, content=message.content)
            for message in previous_messages
            if message.role in {"user", "assistant"} and message.content
        ]
        history.append(HarnessChatMessage(role="user", content=prompt))
        return tuple(history[-MAX_HISTORY_MESSAGES:])

    def _execution_readiness(
        self,
        options: Mapping[str, Any],
        *,
        durable: bool,
    ) -> dict[str, Any]:
        return build_execution_readiness(
            self.config,
            self.registry,
            harness_id=str(options["harness_id"]),
            invocation_mode=options["invocation_mode"],
            execution_transport=options["execution_transport"],
            api_mode=options["api_mode"],
            model=options["model"],
            mode=str(options["mode"]),
            workspace=options["workspace"],
            workspace_policy=options["workspace_policy"],
            durable=durable,
            dry_run=bool(_mapping(options["extra"]).get("dry_run")),
        )

    def _permission_simulation(
        self,
        options: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project effective policy without resolving secrets or starting tools."""
        transport = options.get("execution_transport")
        if not isinstance(transport, ExecutionTransport):
            transport = (
                ExecutionTransport.NATIVE_TERMINAL
                if options["invocation_mode"].value == "native"
                else ExecutionTransport.ONE_SHOT
            )
        extensions = tuple(
            extension_permission_contract(item)
            for item in self._permission_extension_descriptors(options)
        )
        return build_permission_simulation(
            spec=self.registry.get(str(options["harness_id"])).spec(),
            execution_transport=transport,
            invocation_mode=options["invocation_mode"].value,
            permission_profile_id=str(options["permission_profile"]),
            mode=str(options["mode"]),
            workspace=options.get("workspace"),
            api_mode=options["api_mode"].value,
            model=options.get("model"),
            extensions=extensions,
            required_actions=options["required_permission_actions"],
            origin=str(options["permission_origin"]),
        ).to_dict()

    def _permission_extension_descriptors(
        self,
        options: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        """Read exact selected MCP declarations without freezing or resolving them."""
        extra = _mapping(options.get("extra"))
        reference = extra.get("managed_mcp_snapshot")
        store = HeadlessManagedMCPSnapshotStore(self.config.data_dir)
        if isinstance(reference, Mapping):
            snapshot = store.load(reference)
            if snapshot.harness_id != options["harness_id"]:
                raise ValueError("Managed MCP snapshot harness does not match run")
            project = resolve_project(
                options.get("workspace"),
                data_dir=self.config.data_dir,
                load_config_name=False,
            )
            if snapshot.project_id != project.id:
                raise ValueError("Managed MCP snapshot project does not match run")
            return tuple(snapshot.descriptors)
        tool_ids = _managed_tool_ids(extra.get("tool_ids"))
        if not tool_ids:
            return ()
        if options["invocation_mode"].value != "headless":
            raise ValueError(
                "Managed MCP run snapshots currently require headless mode"
            )
        harness_id = str(options["harness_id"])
        if harness_id not in {"codex-cli", "claude-code", "gemini-cli"}:
            raise ValueError(f"{harness_id} does not support managed MCP snapshots")
        project = resolve_project(
            options.get("workspace"),
            data_dir=self.config.data_dir,
            load_config_name=False,
        )
        loaded = load_project_config(project.root)
        descriptors, errors = build_mcp_inventory(loaded.tool_profiles, project=project)
        selected_errors = {
            str(item.get("server_id")): str(item.get("error"))
            for item in errors
            if str(item.get("server_id")) in tool_ids
        }
        if selected_errors:
            details = "; ".join(
                f"{server_id}: {selected_errors[server_id]}"
                for server_id in sorted(selected_errors)
            )
            raise ValueError(f"Managed MCP inventory is invalid: {details}")
        by_id = {item.id: item for item in descriptors}
        missing = sorted(set(tool_ids) - set(by_id))
        if missing:
            raise ValueError(f"Managed MCP servers not found: {', '.join(missing)}")
        selected = tuple(by_id[server_id] for server_id in tool_ids)
        for descriptor in selected:
            if not descriptor.enabled:
                raise ValueError(f"Managed MCP server is disabled: {descriptor.id}")
            if not descriptor.trusted:
                raise ValueError(f"Managed MCP server is not trusted: {descriptor.id}")
            if descriptor.harnesses and harness_id not in descriptor.harnesses:
                raise ValueError(
                    f"Managed MCP server {descriptor.id} is incompatible with "
                    f"{harness_id}"
                )
        return selected

    def _schedule_session_title(
        self,
        session: HarnessSession,
        run_id: str,
        options: Mapping[str, Any],
    ) -> None:
        """Settle one first-turn title without delaying provider execution."""
        if session.title != "Untitled session":
            return
        prompt = str(options["prompt"])
        generation_requested = _generate_session_title_requested(options["extra"])
        model = (
            (
                _optional_text(options["extra"].get("session_title_model"))
                or _optional_text(options.get("model"))
                or self.config.default_model
            )
            if generation_requested
            else None
        )
        timeout_seconds = min(self.config.timeout_seconds, 15.0)
        claim = claim_fallback_title(
            self.store,
            session.id,
            run_id=run_id,
            model=model,
            timeout_seconds=timeout_seconds,
        )
        if claim is None:
            return

        def generate() -> None:
            try:
                generation = _generate_session_title(
                    self.config,
                    prompt,
                    model=model,
                    generation_requested=generation_requested,
                    timeout_seconds=timeout_seconds,
                )
                updated = complete_fallback_title(self.store, claim, generation)
                if updated is None:
                    return
                self._append_title_event(updated, run_id)
            except (OSError, SessionNotFoundError, ValueError):
                return

        if not generation_requested:
            generate()
            return
        threading.Thread(
            target=generate,
            name=f"harness-session-title-{session.id}",
            daemon=True,
        ).start()

    def schedule_session_title(
        self,
        session: HarnessSession,
        run_id: str,
        options: Mapping[str, Any],
    ) -> None:
        """Schedule the shared title contract for non-runner native paths."""
        self._schedule_session_title(session, run_id, options)

    def apply_native_session_title(
        self,
        *,
        session_id: str,
        run_id: str,
        title: str,
        provider: str,
        source_id: str | None,
    ) -> HarnessSession | None:
        """Apply one discovered provider-native title and publish its revision."""
        updated = apply_provider_native_title(
            self.store,
            session_id,
            title=title,
            run_id=run_id,
            provider=provider,
            source_id=source_id,
        )
        if updated is not None:
            self._append_title_event(updated, run_id)
        return updated

    def _append_title_event(self, session: HarnessSession, run_id: str) -> None:
        self._append_event(
            session.id,
            run_id,
            HarnessEventType.SESSION_UPDATED.value,
            "Session title revision stored.",
            {
                "session_id": session.id,
                "revision": session.updated_at,
                "changed_fields": ["title"],
                "title": title_diagnostics(session),
            },
        )

    def _load_attachments(
        self,
        session_id: str,
        attachment_ids: tuple[str, ...],
    ) -> tuple[HarnessAttachment, ...]:
        session = self.store.get_session(session_id)
        shared_parent_session_id = _optional_text(
            session.metadata.get("arena_parent_session_id")
        )
        shared_attachment_session_id = _optional_text(
            session.metadata.get("shared_attachment_session_id")
        )
        allowed_session_ids = {
            session_id,
            shared_parent_session_id,
            shared_attachment_session_id,
        }
        attachments: list[HarnessAttachment] = []
        for attachment_id in attachment_ids:
            try:
                attachment = self.attachment_store.get_attachment(attachment_id)
            except AttachmentNotFoundError as exc:
                raise ValueError(f"Unknown attachment id: {attachment_id}") from exc
            if attachment.session_id not in allowed_session_ids:
                raise ValueError(
                    f"Attachment does not belong to session: {attachment_id}"
                )
            attachments.append(attachment)
        return tuple(attachments)

    def _load_project_memory(
        self,
        workspace: str | None,
    ) -> tuple[ProjectMemoryEntry, ...]:
        if workspace is None:
            return ()
        try:
            project = resolve_project(
                workspace,
                data_dir=self.config.data_dir,
                load_config_name=False,
            )
        except ValueError:
            return ()
        return self.memory_store.enabled_for_prompt(project)

    def _prepare_managed_mcp_snapshot(
        self,
        options: RunOptions,
    ) -> Mapping[str, Any] | None:
        """Resolve or freeze the selected managed tools before run creation."""
        extra = dict(_mapping(options.get("extra")))
        reference = extra.get("managed_mcp_snapshot")
        store = HeadlessManagedMCPSnapshotStore(self.config.data_dir)
        if isinstance(reference, Mapping):
            snapshot = store.load(reference)
            if snapshot.harness_id != options["harness_id"]:
                raise ValueError("Managed MCP snapshot harness does not match run")
            project = resolve_project(
                options.get("workspace"),
                data_dir=self.config.data_dir,
                load_config_name=False,
            )
            if snapshot.project_id != project.id:
                raise ValueError("Managed MCP snapshot project does not match run")
            public_ref = snapshot.public_ref()
            extra["managed_mcp_snapshot"] = public_ref
            extra["tool_ids"] = list(snapshot.server_ids)
            options["extra"] = extra
            return public_ref
        tool_ids = _managed_tool_ids(extra.get("tool_ids"))
        if not tool_ids:
            return None
        if options["invocation_mode"].value != "headless":
            raise ValueError(
                "Managed MCP run snapshots currently require headless mode"
            )
        if options["harness_id"] not in {
            "codex-cli",
            "claude-code",
            "gemini-cli",
        }:
            raise ValueError(
                f"{options['harness_id']} does not support managed MCP snapshots"
            )
        project = resolve_project(
            options.get("workspace"),
            data_dir=self.config.data_dir,
            load_config_name=False,
        )
        loaded = load_project_config(project.root)
        descriptors, errors = build_mcp_inventory(loaded.tool_profiles, project=project)
        selected_errors = {
            str(item.get("server_id")): str(item.get("error"))
            for item in errors
            if str(item.get("server_id")) in tool_ids
        }
        if selected_errors:
            details = "; ".join(
                f"{server_id}: {selected_errors[server_id]}"
                for server_id in sorted(selected_errors)
            )
            raise ValueError(f"Managed MCP inventory is invalid: {details}")
        snapshot = store.create(
            project_id=project.id,
            harness_id=options["harness_id"],
            descriptors=descriptors,
            server_ids=tool_ids,
        )
        public_ref = snapshot.public_ref()
        extra["managed_mcp_snapshot"] = public_ref
        extra["tool_ids"] = list(snapshot.server_ids)
        options["extra"] = extra
        return public_ref

    def _append_event(
        self,
        session_id: str,
        run_id: str,
        event_type: str,
        message: str,
        payload: Mapping[str, Any],
    ) -> HarnessStoredEvent:
        return self.persistence_service.append_event(
            session_id,
            run_id,
            event_type,
            message,
            payload,
        )

    def _export_session_bundle(self, session_id: str) -> HarnessSessionBundle:
        exporter = getattr(self.store, "export_session_bundle", None)
        if callable(exporter):
            return exporter(session_id)
        return self.store.get_session_bundle(session_id)


def _native_resume_metadata(harness_id: str) -> dict[str, Any]:
    if harness_id in {"codex-cli", "claude-code", "gemini-cli"}:
        return {
            "supported": False,
            "reason": "normalized gpt2giga history is enabled; native resume is not implemented yet",
        }
    return {
        "supported": False,
        "reason": "native sessions do not apply to this harness",
    }


def _validate_continuation_identity(
    session: HarnessSession,
    options: Mapping[str, Any],
) -> None:
    """Reject incompatible structured continuation before run side effects."""
    if session.metadata.get("app_server_fork"):
        return
    link = _mapping(session.metadata.get("app_server_thread"))
    snapshot = _mapping(link.get("snapshot"))
    if not link or options.get("harness_id") != "codex-cli":
        return
    extra = _mapping(options.get("extra"))
    managed_mcp = _mapping(extra.get("managed_mcp_snapshot"))
    actual = {
        "harness_id": options.get("harness_id"),
        "api_mode": getattr(options.get("api_mode"), "value", options.get("api_mode")),
        "model": options.get("model"),
        "source_workspace": options.get("workspace"),
        "permission_mode": options.get("mode"),
        "tool_snapshot_hash": managed_mcp.get("snapshot_hash"),
    }
    mismatched = [key for key, value in actual.items() if snapshot.get(key) != value]
    if mismatched:
        raise ValueError(
            "Codex app-server continuation changed "
            + ", ".join(mismatched)
            + "; fork explicitly."
        )


def _edit_message_id(options: Mapping[str, Any]) -> str | None:
    return _optional_text(_mapping(options.get("extra")).get("edit_message_id"))


def _edit_continuation_source(
    store: HarnessSessionStore,
    *,
    edit_message_id: str | None,
    previous_messages: tuple[HarnessMessage, ...],
) -> Mapping[str, Any] | None:
    if edit_message_id is None:
        return None
    for message in reversed(previous_messages):
        if message.run_id is None:
            continue
        try:
            run = store.get_run(message.run_id)
        except KeyError:
            continue
        link = _mapping(run.metadata.get("app_server_thread"))
        if link.get("thread_id"):
            return {
                "action": "fork",
                "link": link,
                "thread_id": link["thread_id"],
                "turn_id": link.get("latest_turn_id"),
            }
    return {"action": "start"}


def _project_metadata(workspace: str | None, *, data_dir: str) -> dict[str, str]:
    if workspace is None:
        return {}
    project = resolve_project(workspace, data_dir=data_dir)
    return {
        "project_id": project.id,
        "project_root": project.root,
        "project_name": project.name,
    }


def _prompt_with_project_memory(
    prompt: str,
    entries: tuple[ProjectMemoryEntry, ...],
) -> str:
    if not entries:
        return prompt
    memory_text = memory_entries_to_prompt(entries)
    return (
        f"Project memory to honor for this run:\n{memory_text}\n\nUser task:\n{prompt}"
    )


def _generate_session_title_requested(extra: Mapping[str, Any]) -> bool:
    return bool(extra.get("generate_session_title"))


def _generate_session_title(
    config: HarnessConfig,
    prompt: str,
    *,
    model: str | None,
    generation_requested: bool,
    timeout_seconds: float,
) -> SessionTitleGeneration:
    """Generate a compact title through the local proxy with a safe fallback."""
    started_at = time.monotonic()
    fallback = title_from_prompt(prompt)
    if not generation_requested:
        return SessionTitleGeneration(
            title=fallback,
            status="disabled",
            duration_ms=(time.monotonic() - started_at) * 1000,
            usage={},
        )
    if model is None:
        return SessionTitleGeneration(
            title=fallback,
            status="offline",
            duration_ms=(time.monotonic() - started_at) * 1000,
            usage={},
            failure_kind="model_unavailable",
        )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Create a concise session title of 3 to 7 words in the user's "
                    "language. Return only the title without quotes or punctuation."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": 32,
    }
    api_key = config.api_key or proxy.cached_sidecar_api_key(config.proxy_url)
    try:
        response = proxy.request_json(
            "POST",
            proxy.build_chat_completions_url(config.proxy_url, GigaChatApiMode.V2),
            payload=payload,
            api_key=api_key,
            timeout=timeout_seconds,
        )
    except (OSError, proxy.ProxyRequestError, ValueError) as exc:
        return SessionTitleGeneration(
            title=fallback,
            status="failed",
            duration_ms=(time.monotonic() - started_at) * 1000,
            usage={},
            failure_kind=type(exc).__name__,
        )
    generated = proxy.extract_text(response).strip().strip("\"'`").strip()
    return SessionTitleGeneration(
        title=title_from_prompt(generated) if generated else fallback,
        status="succeeded" if generated else "failed",
        duration_ms=(time.monotonic() - started_at) * 1000,
        usage=_title_usage(response),
        failure_kind=None if generated else "empty_response",
    )


def _title_usage(response: Mapping[str, Any]) -> dict[str, int]:
    usage = _mapping(response.get("usage"))
    aliases = {
        "prompt_tokens": "input_tokens",
        "input_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    result: dict[str, int] = {}
    for source, target in aliases.items():
        value = usage.get(source)
        if (
            target not in result
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        ):
            result[target] = value
    return result


def _provider_native_title(result: Any) -> tuple[str, str | None] | None:
    raw = _mapping(result.raw)
    title = _optional_text(raw.get("provider_session_title"))
    source_id = _optional_text(raw.get("provider_session_id"))
    if title is not None:
        return title, source_id
    for event in reversed(tuple(result.events)):
        if event.type not in {
            "session.title.updated",
            "thread.title.updated",
            "thread.name.updated",
        }:
            continue
        payload = _mapping(event.payload)
        title = _optional_text(payload.get("title") or payload.get("name"))
        if title is not None:
            return (
                title,
                _optional_text(
                    payload.get("session_id")
                    or payload.get("thread_id")
                    or payload.get("native_session_id")
                ),
            )
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _attachment_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("attachment_ids must be a list")
    ids: list[str] = []
    for item in value:
        attachment_id = _optional_text(item)
        if attachment_id is None:
            raise ValueError("attachment_ids must contain non-empty strings")
        ids.append(attachment_id)
    return tuple(ids)


def _managed_tool_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("tool_ids must be a list")
    result: list[str] = []
    for item in value:
        server_id = _optional_text(item)
        if server_id is None:
            raise ValueError("tool_ids must contain non-empty strings")
        if server_id not in result:
            result.append(server_id)
    return tuple(result)


def _permission_actions(value: Any) -> tuple[PermissionAction, ...]:
    """Parse an optional strengthening-only route requirement list."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("required_permission_actions must be a list")
    parsed = tuple(PermissionAction(str(item)) for item in value)
    if len(set(parsed)) != len(parsed):
        raise ValueError("required_permission_actions contains duplicates")
    return tuple(sorted(parsed, key=lambda item: item.value))


def _agent_metadata(options: Mapping[str, Any]) -> dict[str, Any]:
    """Return immutable, redacted AgentProfile identity for run history."""
    agent_id = _optional_text(options.get("agent_id"))
    snapshot = options.get("agent_profile_snapshot")
    if agent_id is None or not isinstance(snapshot, Mapping):
        return {}
    metadata = {
        "agent_id": agent_id,
        "agent_profile_snapshot": dict(snapshot),
    }
    execution_plan = options.get("agent_execution_plan")
    if isinstance(execution_plan, Mapping):
        metadata["agent_execution_plan"] = dict(execution_plan)
    return metadata


def _workbench_admission_metadata(options: Mapping[str, Any]) -> dict[str, Any]:
    """Retain the content-free product admission receipt on each run."""
    admission = _mapping(_mapping(options.get("extra")).get("workbench_admission"))
    if admission.get("schema_version") != 1:
        return {}
    return {"workbench_admission": dict(admission)}


def _workbench_session_selection_metadata(
    options: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain explicit intent and authority independently from the mode alias."""
    admission = _mapping(_mapping(options.get("extra")).get("workbench_admission"))
    if admission.get("schema_version") != 1:
        return {}
    diagnostics = _mapping(admission.get("diagnostics"))
    compatibility = _mapping(diagnostics.get("compatibility"))
    return {
        "workbench_selection": {
            "schema_version": 1,
            "kind": admission.get("kind"),
            "intent": admission.get("intent"),
            "authority": admission.get("authority"),
            "input_source": admission.get("input_source"),
            "compatibility_warning": compatibility.get("warning"),
        }
    }


def _request_extra(
    extra: Mapping[str, Any],
    attachments: tuple[Mapping[str, Any], ...],
    attachment_render_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(extra)
    payload.pop("edit_message_id", None)
    if attachments:
        payload["attachment_ids"] = [
            str(attachment["id"]) for attachment in attachments
        ]
        payload["attachments"] = [dict(attachment) for attachment in attachments]
    if attachment_render_plan:
        payload["attachment_render_plan"] = dict(attachment_render_plan)
    return payload
