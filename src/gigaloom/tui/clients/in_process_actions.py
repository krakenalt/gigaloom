"""In-process application and environment action adapter methods."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator, Mapping


from gigaloom.attachments import (
    attachment_to_dict,
)
from gigaloom.claude_handoff import (
    ClaudeHandoffAction,
    ClaudeHandoffLaunchMode,
    claude_handoff_plan_to_dict,
)
from gigaloom.project import (
    resolve_project,
)
from gigaloom.workbench_protocol import (
    WorkbenchStatePage,
)
from gigaloom.workbench_resources import (
    PreferenceSnapshot,
    ProcessProjection,
    TaskProjection,
    WorkbenchResourceSnapshot,
    process_binding,
    task_binding,
)
from gigaloom.workspace import workspace_tree

from gigaloom.tui.contracts import (
    MAX_FILE_CANDIDATES,
    WorkbenchClientError,
    EnvironmentCommitPreviewSummary,
    EnvironmentCommitApplySummary,
    EnvironmentPushPreviewSummary,
    EnvironmentPushApplySummary,
    EnvironmentPullRequestPreviewSummary,
    EnvironmentPullRequestApplySummary,
    FileCandidate,
    AttachmentSummary,
    RunInspection,
    HandoffPreview,
    NativeTerminalSnapshot,
)

from gigaloom.tui.projections.values import (
    _optional_text,
    _display_text,
    _required_identity,
    _required_content,
)


from gigaloom.tui.projections.environment import (
    _environment_commit_preview_summary,
    _environment_commit_outcome_summary,
    _environment_push_preview_summary,
    _environment_push_outcome_summary,
    _environment_pull_request_preview_summary,
    _environment_pull_request_outcome_summary,
)

from gigaloom.tui.projections.resources import (
    _workspace_limits,
    _session_workspace,
    _attachment_ids,
    _file_candidate,
    _attachment_summary,
    _provider_handoff_from_mapping,
    _blocked_provider_handoff,
)

from gigaloom.tui.projections.run_inspection import _in_process_run_inspection


from gigaloom.tui.projections.native import (
    _native_terminal_input,
    _native_terminal_dimensions,
    _in_process_native_terminal_error,
)


class _InProcessActionsMixin:
    """Cohesive non-run actions for the in-process transport."""

    async def workbench_state(
        self,
        *,
        cursor: str | None = None,
        limit: int = 32,
    ) -> WorkbenchStatePage:
        """Read the same bounded backbone contract used by attach mode."""
        return self.workbench_backbone.read(cursor, limit=limit)

    async def preview_environment_commit(
        self,
        workspace: str,
        *,
        message: str,
        author_name: str,
        author_email: str,
    ) -> EnvironmentCommitPreviewSummary:
        if self.environment_commit_service is None:
            raise WorkbenchClientError("Git commit action is unavailable")
        preview = await asyncio.to_thread(
            self.environment_commit_service.preview,
            workspace,
            message=message,
            author_name=author_name,
            author_email=author_email,
        )
        return _environment_commit_preview_summary(preview.to_dict())

    async def apply_environment_commit(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentCommitApplySummary:
        if (
            self.environment_commit_service is None
            or self.governed_environment_commit_service is None
        ):
            raise WorkbenchClientError("Git commit action is unavailable")
        preview = self.environment_commit_service.get_preview(preview_id)
        resolved = Path(workspace).expanduser().resolve()
        root = Path(preview.worktree_root).resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise WorkbenchClientError("workspace changed after commit preview")
        project_id = preview.scope_id
        if session_id is not None:
            session = self.store.get_session(session_id)
            project_id = str(session.metadata.get("project_id") or "") or project_id
        try:
            outcome = await asyncio.to_thread(
                self.governed_environment_commit_service.apply_or_request,
                preview_id,
                project_id=project_id,
                session_id=session_id,
            )
        except RuntimeError as exc:
            raise WorkbenchClientError(str(exc)) from exc
        return _environment_commit_outcome_summary(outcome)

    async def decide_environment_approval(
        self, approval_id: str, decision: str
    ) -> None:
        self.sessions.decide_approval(approval_id, decision)

    async def preview_environment_push(
        self, workspace: str
    ) -> EnvironmentPushPreviewSummary:
        if self.environment_push_service is None:
            raise WorkbenchClientError("Git push action is unavailable")
        preview = await asyncio.to_thread(
            self.environment_push_service.preview,
            workspace,
        )
        return _environment_push_preview_summary(preview.to_dict())

    async def apply_environment_push(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPushApplySummary:
        if (
            self.environment_push_service is None
            or self.governed_environment_push_service is None
        ):
            raise WorkbenchClientError("Git push action is unavailable")
        preview = self.environment_push_service.get_preview(preview_id)
        resolved = Path(workspace).expanduser().resolve()
        root = Path(preview.worktree_root).resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise WorkbenchClientError("workspace changed after push preview")
        project_id = preview.scope_id
        if session_id is not None:
            session = self.store.get_session(session_id)
            project_id = str(session.metadata.get("project_id") or "") or project_id
        try:
            outcome = await asyncio.to_thread(
                self.governed_environment_push_service.apply_or_request,
                preview_id,
                project_id=project_id,
                session_id=session_id,
            )
        except RuntimeError as exc:
            raise WorkbenchClientError(str(exc)) from exc
        return _environment_push_outcome_summary(outcome)

    async def preview_environment_pull_request(
        self,
        workspace: str,
        *,
        title: str,
        body: str,
        base_branch: str | None = None,
    ) -> EnvironmentPullRequestPreviewSummary:
        if self.environment_pull_request_service is None:
            raise WorkbenchClientError("Pull-request action is unavailable")
        preview = await asyncio.to_thread(
            self.environment_pull_request_service.preview,
            workspace,
            title=title,
            body=body,
            base_branch=base_branch,
        )
        return _environment_pull_request_preview_summary(preview.to_dict())

    async def apply_environment_pull_request(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPullRequestApplySummary:
        if (
            self.environment_pull_request_service is None
            or self.governed_environment_pull_request_service is None
        ):
            raise WorkbenchClientError("Pull-request action is unavailable")
        preview = self.environment_pull_request_service.get_preview(preview_id)
        resolved = Path(workspace).expanduser().resolve()
        root = Path(preview.worktree_root).resolve()
        if resolved != root and not resolved.is_relative_to(root):
            raise WorkbenchClientError("workspace changed after pull-request preview")
        project_id = preview.scope_id
        if session_id is not None:
            session = self.store.get_session(session_id)
            project_id = str(session.metadata.get("project_id") or "") or project_id
        try:
            outcome = await asyncio.to_thread(
                self.governed_environment_pull_request_service.apply_or_request,
                preview_id,
                project_id=project_id,
                session_id=session_id,
            )
        except RuntimeError as exc:
            raise WorkbenchClientError(str(exc)) from exc
        return _environment_pull_request_outcome_summary(outcome)

    async def resources(
        self, session_id: str | None = None
    ) -> WorkbenchResourceSnapshot:
        """Read resources through the same application authority as attach mode."""
        return self.resource_service.snapshot(session_id)

    async def cancel_task(self, task: TaskProjection) -> TaskProjection:
        """Request exact durable task cancellation."""
        return self.resource_service.cancel_task(task_binding(task))

    async def stop_process(self, process: ProcessProjection) -> ProcessProjection:
        """Fail closed because in-process mode does not own native supervision."""
        self.resource_service.validate_process(process_binding(process))
        raise WorkbenchClientError(
            "native process control requires attach mode; ownership is server-side"
        )

    async def save_preferences(
        self, values: Mapping[str, Any], *, expected_revision: str
    ) -> PreferenceSnapshot:
        """Persist private Workbench-only preferences."""
        return self.resource_service.save_preferences(
            values, expected_revision=expected_revision
        )

    async def search_files(
        self, session_id: str, query: str
    ) -> tuple[FileCandidate, ...]:
        session = self.store.get_session(session_id)
        workspace = _session_workspace(session)
        limits = _workspace_limits(workspace)
        files = workspace_tree(
            workspace,
            query=query,
            limits=limits,
            result_limit=MAX_FILE_CANDIDATES,
        )
        return tuple(
            _file_candidate(item, workspace=workspace, limits=limits) for item in files
        )

    async def attach_file(self, session_id: str, path: str) -> AttachmentSummary:
        session = self.store.get_session(session_id)
        workspace = _session_workspace(session)
        project_id = _optional_text(session.metadata.get("project_id"))
        if project_id is None:
            project_id = resolve_project(
                workspace,
                data_dir=self.config.data_dir,
                load_config_name=False,
            ).id
        attachment = self.attachment_store.create_workspace_reference(
            session_id=session.id,
            project_id=project_id,
            workspace_root=workspace,
            path=path,
            limits=_workspace_limits(workspace),
        )
        return _attachment_summary(attachment_to_dict(attachment))

    async def inspect_run(self, run_id: str) -> RunInspection:
        run = self.sessions.get_run(run_id)
        return _in_process_run_inspection(
            run,
            registry=self.registry,
            runtime_store=self.runtime_store,
        )

    async def provider_handoff(self, session_id: str) -> HandoffPreview:
        session = self.store.get_session(session_id)
        workspace = _session_workspace(session)
        harness = self.registry.get(session.default_harness_id)
        preview = getattr(harness, "provider_handoff_preview", None)
        if not callable(preview):
            return _blocked_provider_handoff(session.default_harness_id)
        try:
            plan = preview(
                action=ClaudeHandoffAction.OPEN_PROVIDER_UI,
                workspace=workspace,
                launch_mode=ClaudeHandoffLaunchMode.INTERACTIVE,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return HandoffPreview(
                kind="provider",
                status="blocked",
                target=session.default_harness_id,
                continuity="Harness session remains authoritative and unchanged.",
                observability=("provider target was not opened",),
                instruction=_display_text(str(exc)),
            )
        return _provider_handoff_from_mapping(
            claude_handoff_plan_to_dict(plan),
            harness_id=session.default_harness_id,
        )

    async def web_handoff(self, session_id: str) -> HandoffPreview:
        self.store.get_session(session_id)
        return HandoffPreview(
            kind="web",
            status="blocked",
            target="unavailable in in-process mode",
            continuity=f"Session {session_id} remains durable in the local store.",
            observability=(
                "no FastAPI or uvicorn server is started by the terminal client",
            ),
            instruction="Use attach mode with an already running local Web application.",
        )

    async def start_native_terminal(
        self,
        session_id: str,
        content: str,
        *,
        idempotency_key: str,
        attachment_ids: tuple[str, ...] = (),
        harness_id: str | None = None,
        model: str | None = None,
        api_mode: str | None = None,
        mode: str | None = None,
    ) -> NativeTerminalSnapshot:
        self.store.get_session(session_id)
        _required_content(content, "native terminal prompt")
        _required_identity(idempotency_key, "idempotency key")
        _attachment_ids(attachment_ids)
        raise WorkbenchClientError(
            "native terminal start requires attach mode so the existing policy, "
            "worktree, and process application authority remains the sole owner"
        )

    async def snapshot_native_terminal(
        self, process_id: str, *, cursor: int = 0
    ) -> NativeTerminalSnapshot:
        raise _in_process_native_terminal_error(process_id, cursor=cursor)

    async def stream_native_terminal(
        self,
        process_id: str,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[NativeTerminalSnapshot]:
        if False:  # pragma: no cover - preserve the async-iterator contract.
            yield await self.snapshot_native_terminal(process_id, cursor=cursor)
        raise _in_process_native_terminal_error(process_id, cursor=cursor)

    async def status_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        raise _in_process_native_terminal_error(process_id)

    async def send_native_terminal_input(
        self, process_id: str, data: str, *, submit: bool = False
    ) -> NativeTerminalSnapshot:
        _native_terminal_input(data)
        raise _in_process_native_terminal_error(process_id)

    async def resize_native_terminal(
        self, process_id: str, *, rows: int, columns: int
    ) -> NativeTerminalSnapshot:
        _native_terminal_dimensions(rows, columns)
        raise _in_process_native_terminal_error(process_id)

    async def stop_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        raise _in_process_native_terminal_error(process_id)
