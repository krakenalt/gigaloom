"""In-process TUI workbench transport adapter."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import threading
from typing import Any


from gigaloom.attachments import (
    FilesystemAttachmentStore,
)
from gigaloom.application import SessionApplicationService
from gigaloom.config import HarnessConfig
from gigaloom.environment_actions import (
    EnvironmentCommitError,
    EnvironmentCommitService,
    GovernedEnvironmentCommitService,
)
from gigaloom.environment_push import (
    EnvironmentPushError,
    EnvironmentPushService,
    GovernedEnvironmentPushService,
)
from gigaloom.environment_pull_requests import (
    EnvironmentPullRequestError,
    EnvironmentPullRequestService,
    GovernedEnvironmentPullRequestService,
)
from gigaloom.github_environments import (
    GitHubEnvironmentService,
)
from gigaloom.integration_flows import IntegrationFlowService
from gigaloom.project import (
    HarnessProject,
    load_project_state,
    resolve_project,
    update_project_state,
)
from gigaloom.registry import HarnessRegistry, create_default_registry
from gigaloom.runtime.policy import PolicyEngine
from gigaloom.runtime.store import RuntimeCoordinationStore
from gigaloom.session_runner import HarnessSessionRunner
from gigaloom.session_exports import write_session_export
from gigaloom.sessions import FilesystemHarnessSessionStore
from gigaloom.sessions.models import (
    HarnessSession,
    session_to_dict,
)
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.settings import HarnessSettingsStore
from gigaloom.types import availability_to_dict, spec_to_dict
from gigaloom.workbench_execution import workbench_transport_projection
from gigaloom.workbench_protocol import (
    WorkbenchBackbone,
)
from gigaloom.workbench_resources import (
    WorkbenchPreferenceStore,
    WorkbenchResourceService,
)

from gigaloom.tui.contracts import (
    MAX_PROJECTS,
    MAX_SESSIONS,
    WorkbenchClientError,
    ProjectSummary,
    SessionSummary,
    SessionActionBinding,
    SessionPreview,
    SessionExport,
    HarnessSummary,
    ReadinessSummary,
    NavigationSnapshot,
)

from gigaloom.tui.projections.values import _required_content

from gigaloom.tui.projections.navigation import (
    _in_process_integration_summary,
    _selected_session_id,
    _project_summary,
    _session_summary,
    _message_preview,
    _session_export_text,
    _harness_summary,
    _readiness_summary,
)

from gigaloom.tui.projections.environment import _capture_environment_summary


from gigaloom.tui.clients.in_process_actions import _InProcessActionsMixin
from gigaloom.tui.clients.in_process_runs import _InProcessRunsMixin
from gigaloom.tui.clients.session_queries import session_preview_messages


class InProcessWorkbenchClient(_InProcessActionsMixin, _InProcessRunsMixin):
    """Use existing application services without FastAPI, uvicorn, or a daemon."""

    transport_mode = "in_process"

    def __init__(
        self,
        config: HarnessConfig,
        *,
        registry: HarnessRegistry | None = None,
        store: FilesystemHarnessSessionStore | None = None,
        github_environment_service: GitHubEnvironmentService | None = None,
        environment_push_service: EnvironmentPushService | None = None,
        environment_pull_request_service: EnvironmentPullRequestService | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or create_default_registry()
        self.store = store or FilesystemHarnessSessionStore(config.data_dir)
        runner = HarnessSessionRunner(
            registry=self.registry,
            config=config,
            store=self.store,
        )
        self.settings_store = HarnessSettingsStore(config.data_dir, config)
        self.runtime_store = RuntimeCoordinationStore(config.data_dir)
        try:
            self.environment_commit_service = EnvironmentCommitService(config.data_dir)
        except EnvironmentCommitError:
            self.environment_commit_service = None
        self.governed_environment_commit_service = (
            GovernedEnvironmentCommitService(
                self.environment_commit_service,
                self.runtime_store,
                PolicyEngine(self.runtime_store),
            )
            if self.environment_commit_service is not None
            else None
        )
        if environment_push_service is None:
            try:
                environment_push_service = EnvironmentPushService(config.data_dir)
            except EnvironmentPushError:
                environment_push_service = None
        self.environment_push_service = environment_push_service
        self.governed_environment_push_service = (
            GovernedEnvironmentPushService(
                self.environment_push_service,
                self.runtime_store,
                PolicyEngine(self.runtime_store),
            )
            if self.environment_push_service is not None
            else None
        )
        if environment_pull_request_service is None:
            try:
                environment_pull_request_service = EnvironmentPullRequestService(
                    config.data_dir
                )
            except EnvironmentPullRequestError:
                environment_pull_request_service = None
        self.environment_pull_request_service = environment_pull_request_service
        self.governed_environment_pull_request_service = (
            GovernedEnvironmentPullRequestService(
                self.environment_pull_request_service,
                self.runtime_store,
                PolicyEngine(self.runtime_store),
            )
            if self.environment_pull_request_service is not None
            else None
        )
        self.attachment_store = FilesystemAttachmentStore(config.data_dir)
        self.integration_service = IntegrationFlowService(config.data_dir)
        self.github_environment_service = (
            github_environment_service or GitHubEnvironmentService()
        )
        self.resource_service = WorkbenchResourceService(
            session_store=self.store,
            runtime_store=self.runtime_store,
            preference_store=WorkbenchPreferenceStore(config.data_dir),
            integration_service=self.integration_service,
        )
        self.sessions = SessionApplicationService(
            runner=runner,
            settings_store=self.settings_store,
            runtime_store=self.runtime_store,
        )
        self._active_runs: dict[str, tuple[asyncio.Task[Any], threading.Event]] = {}
        self._submitted_turns: dict[str, str] = {}
        self._session_mutations: dict[str, SessionSummary | SessionExport | None] = {}
        self.workbench_backbone = WorkbenchBackbone()

    async def load(
        self,
        workspace: str | None,
        *,
        selected_session_id: str | None = None,
    ) -> NavigationSnapshot:
        if workspace is None and selected_session_id is not None:
            workspace = self.store.get_session(selected_session_id).workspace
        project = resolve_project(workspace, data_dir=self.config.data_dir)
        sessions = self.store.list_sessions(
            workspace=project.root,
            include_archived=False,
            limit=MAX_SESSIONS,
        )
        selected_id = _selected_session_id(
            sessions,
            selected_session_id or load_project_state(project).last_selected_session,
        )
        selected = next(
            (item for item in sessions if item.id == selected_id),
            None,
        )
        harnesses = tuple(
            _harness_summary(
                spec_to_dict(harness.spec()),
                availability_to_dict(harness.availability()),
                workbench_transport_projection(harness),
            )
            for harness in self.registry.list()
        )
        readiness = self._readiness(project, selected, harnesses)
        environment = await asyncio.to_thread(
            _capture_environment_summary,
            project.root,
            self.github_environment_service,
        )
        projects = self._projects(project)
        project_summary = next(item for item in projects if item.id == project.id)
        return NavigationSnapshot(
            transport_mode=self.transport_mode,
            projects=projects,
            project=project_summary,
            sessions=tuple(_session_summary(item) for item in sessions),
            selected_session_id=selected_id,
            harnesses=harnesses,
            readiness=readiness,
            integrations=_in_process_integration_summary(self.integration_service),
            environment=environment,
        )

    async def create_session(
        self,
        workspace: str,
        *,
        title: str | None = None,
        harness_id: str | None = None,
        model: str | None = None,
        api_mode: str | None = None,
        mode: str | None = None,
    ) -> SessionSummary:
        payload = {
            "workspace": workspace,
            **({"title": title} if title else {}),
            **({"harness_id": harness_id} if harness_id else {}),
            **({"model": model} if model else {}),
            **({"api_mode": api_mode} if api_mode else {}),
            **({"mode": mode} if mode else {}),
        }
        session = self.sessions.create_session(
            payload,
            validate_harness=harness_id is not None,
        )
        await self.remember_session(workspace, session.id)
        return _session_summary(session)

    async def remember_session(self, workspace: str, session_id: str) -> None:
        project = resolve_project(
            workspace,
            data_dir=self.config.data_dir,
            load_config_name=False,
        )
        update_project_state(project, {"last_selected_session": session_id})

    async def search_sessions(
        self,
        query: str = "",
        *,
        provider: str | None = None,
        project: str | None = None,
        include_archived: bool = True,
    ) -> tuple[SessionSummary, ...]:
        sessions = self.store.list_sessions(
            project_id=project,
            harness_id=provider,
            q=query.strip() or None,
            include_archived=include_archived,
            limit=MAX_SESSIONS,
        )
        return tuple(_session_summary(item, self.store) for item in sessions)

    async def preview_session(
        self, session_id: str, *, transcript_query: str = ""
    ) -> SessionPreview:
        session = self.store.get_session(session_id)
        messages, match_count, truncated = session_preview_messages(
            self.store,
            session_id,
            transcript_query,
        )
        return SessionPreview(
            session=_session_summary(session, self.store),
            transcript=tuple(_message_preview(item) for item in messages),
            match_count=match_count,
            truncated=truncated,
        )

    async def rename_session(
        self, binding: SessionActionBinding, title: str
    ) -> SessionSummary:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionSummary):
            return cached
        self._validate_session_binding(binding)
        updated = self.store.update_session_if_revision(
            binding.session_id,
            binding.revision,
            title=_required_content(title, "session title"),
        )
        if updated is None:
            raise WorkbenchClientError(
                "session changed; authoritative resnapshot required"
            )
        result = _session_summary(updated, self.store)
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def archive_session(
        self, binding: SessionActionBinding, *, archived: bool = True
    ) -> SessionSummary:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionSummary):
            return cached
        self._validate_session_binding(binding)
        updated = self.store.update_session_if_revision(
            binding.session_id,
            binding.revision,
            archived=archived,
        )
        if updated is None:
            raise WorkbenchClientError(
                "session changed; authoritative resnapshot required"
            )
        result = _session_summary(updated, self.store)
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def delete_session(self, binding: SessionActionBinding) -> None:
        if binding.idempotency_key in self._session_mutations:
            return
        self._validate_session_binding(binding, require_idle=True)
        if not self.store.delete_session_if_revision(
            binding.session_id, binding.revision
        ):
            raise WorkbenchClientError(
                "session changed; authoritative resnapshot required"
            )
        self._session_mutations[binding.idempotency_key] = None

    async def fork_session(self, binding: SessionActionBinding) -> SessionSummary:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionSummary):
            return cached
        source = self._validate_session_binding(binding)
        source_summary = _session_summary(source, self.store)
        reference = {
            key: value
            for key, value in {
                "authority": source_summary.native_authority,
                "native_id": source_summary.native_session_id,
                "workspace": source.workspace,
                "operation": "fork",
            }.items()
            if value is not None
        }
        metadata = {
            **dict(source.metadata),
            "forked_from_session_id": source.id,
            "fork_semantics": "harness_replay",
            **({"native_session_reference": reference} if reference else {}),
        }
        metadata.pop("structured_session_link", None)
        fork = self.store.create_session(
            title=f"Fork: {source.title}",
            workspace=source.workspace,
            default_harness_id=source.default_harness_id,
            default_model=source.default_model,
            default_api_mode=source.default_api_mode,
            default_mode=source.default_mode,
            metadata=metadata,
        )
        for message in self.store.list_messages(source.id):
            self.store.append_message(
                replace(
                    message,
                    id=new_id("msg"),
                    session_id=fork.id,
                    run_id=None,
                    created_at=utc_now(),
                    metadata={
                        **dict(message.metadata),
                        "forked_from_message_id": message.id,
                    },
                )
            )
        result = _session_summary(fork, self.store)
        self._session_mutations[binding.idempotency_key] = result
        return result

    async def export_session(self, binding: SessionActionBinding) -> SessionExport:
        cached = self._session_mutations.get(binding.idempotency_key)
        if isinstance(cached, SessionExport):
            return cached
        session = self._validate_session_binding(binding)
        messages = self.store.list_messages(session.id)
        body = _session_export_text(session, messages)
        path = write_session_export(Path(self.config.data_dir) / "exports", body)
        result = SessionExport(session.id, str(path), len(messages))
        self._session_mutations[binding.idempotency_key] = result
        return result

    def _validate_session_binding(
        self,
        binding: SessionActionBinding,
        *,
        require_idle: bool = False,
    ) -> HarnessSession:
        session = self.store.get_session(binding.session_id)
        summary = _session_summary(session, self.store)
        if (
            summary.revision != binding.revision
            or summary.generation != binding.generation
            or summary.lease != binding.lease
        ):
            raise WorkbenchClientError(
                "session changed; authoritative resnapshot required"
            )
        if require_idle and binding.lease is not None:
            raise WorkbenchClientError("active session lease blocks destructive action")
        return session

    def _projects(self, current: HarnessProject) -> tuple[ProjectSummary, ...]:
        projects: dict[str, HarnessProject] = {current.id: current}
        for session in self.store.list_sessions(
            include_archived=False,
            limit=MAX_SESSIONS,
        ):
            if not session.workspace:
                continue
            try:
                project = resolve_project(
                    session.workspace,
                    data_dir=self.config.data_dir,
                    load_config_name=False,
                )
            except (OSError, ValueError):
                continue
            projects.setdefault(project.id, project)
            if len(projects) >= MAX_PROJECTS:
                break
        counts = {project_id: 0 for project_id in projects}
        for session in self.store.list_sessions(
            include_archived=False,
            limit=MAX_SESSIONS,
        ):
            project_id = str(session.metadata.get("project_id") or "")
            if project_id in counts:
                counts[project_id] += 1
        ordered = sorted(
            projects.values(),
            key=lambda item: (item.id != current.id, item.name.lower(), item.id),
        )
        return tuple(
            _project_summary(item, session_count=counts.get(item.id, 0))
            for item in ordered
        )

    def _readiness(
        self,
        project: HarnessProject,
        session: HarnessSession | None,
        harnesses: tuple[HarnessSummary, ...],
    ) -> ReadinessSummary:
        defaults = self.settings_store.load().defaults
        harness_id = (
            session.default_harness_id if session else defaults.default_harness_id
        )
        model = session.default_model if session else defaults.default_model
        payload = {
            "prompt": "",
            "workspace": project.root,
            "harness_id": harness_id,
            "model": model,
            "api_mode": (
                session.default_api_mode.value
                if session is not None
                else defaults.default_api_mode
            ),
            "mode": session.default_mode if session is not None else defaults.mode,
            "invocation_mode": defaults.invocation_mode,
            "workspace_policy": defaults.workspace_policy,
            "dry_run": True,
        }
        try:
            prepared = self.sessions.prepare_turn_payload(
                payload,
                session_id=session.id if session else None,
            )
            report = self.sessions.runner.preflight(
                prepared,
                session_id=session.id if session else None,
                durable=False,
            )
            readiness = dict(report.readiness)
        except (KeyError, OSError, ValueError) as exc:
            readiness = {
                "status": "blocked",
                "findings": ({"status": "blocked", "id": type(exc).__name__},),
                "plan": {"execution_transport": defaults.execution_transport},
            }
        return _readiness_summary(
            readiness,
            session=session_to_dict(session) if session else None,
            harnesses=harnesses,
            harness_id=harness_id,
            model=model,
        )
