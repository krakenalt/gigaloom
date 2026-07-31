"""Transport-neutral workbench client protocol."""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping, Protocol


from gigaloom.workbench_protocol import (
    WorkbenchStatePage,
)
from gigaloom.workbench_resources import (
    PreferenceSnapshot,
    ProcessProjection,
    TaskProjection,
    WorkbenchResourceSnapshot,
)

from gigaloom.tui.contracts import (
    SessionSummary,
    SessionActionBinding,
    SessionPreview,
    SessionExport,
    EnvironmentCommitPreviewSummary,
    EnvironmentCommitApplySummary,
    EnvironmentPushPreviewSummary,
    EnvironmentPushApplySummary,
    EnvironmentPullRequestPreviewSummary,
    EnvironmentPullRequestApplySummary,
    NavigationSnapshot,
    RunActionBinding,
    RunSnapshot,
    FileCandidate,
    AttachmentSummary,
    RunInspection,
    HandoffPreview,
    NativeTerminalSnapshot,
)


class WorkbenchClient(Protocol):
    """Thin asynchronous client contract shared by both transports."""

    async def load(
        self,
        workspace: str | None,
        *,
        selected_session_id: str | None = None,
    ) -> NavigationSnapshot:
        """Load one authoritative navigation snapshot."""

    async def workbench_state(
        self,
        *,
        cursor: str | None = None,
        limit: int = 32,
    ) -> WorkbenchStatePage:
        """Load provider-neutral projections and ordered reconnect deltas."""

    async def resources(
        self, session_id: str | None = None
    ) -> WorkbenchResourceSnapshot:
        """Load bounded tasks, processes, usage, preferences, and inventory."""

    async def cancel_task(self, task: TaskProjection) -> TaskProjection:
        """Cancel one exact task owner, lease, generation, and child identity."""

    async def stop_process(self, process: ProcessProjection) -> ProcessProjection:
        """Stop one exact application-owned process."""

    async def save_preferences(
        self, values: Mapping[str, Any], *, expected_revision: str
    ) -> PreferenceSnapshot:
        """Persist one exact private Workbench preference revision."""

    async def preview_environment_commit(
        self,
        workspace: str,
        *,
        message: str,
        author_name: str,
        author_email: str,
    ) -> EnvironmentCommitPreviewSummary:
        """Create one exact local commit preview."""

    async def apply_environment_commit(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentCommitApplySummary:
        """Request approval or apply the exact commit once."""

    async def decide_environment_approval(
        self, approval_id: str, decision: str
    ) -> None:
        """Decide one hash-bound environment approval."""

    async def preview_environment_push(
        self, workspace: str
    ) -> EnvironmentPushPreviewSummary:
        """Create one exact local/remote push preview."""

    async def apply_environment_push(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPushApplySummary:
        """Request approval or push the exact commit once."""

    async def preview_environment_pull_request(
        self,
        workspace: str,
        *,
        title: str,
        body: str,
        base_branch: str | None = None,
    ) -> EnvironmentPullRequestPreviewSummary:
        """Create one exact hosted pull-request preview."""

    async def apply_environment_pull_request(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPullRequestApplySummary:
        """Request approval or create the exact pull request once."""

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
        """Create a session using explicit intent plus backend-owned defaults."""

    async def remember_session(self, workspace: str, session_id: str) -> None:
        """Persist the selected session through the existing project state."""

    async def search_sessions(
        self,
        query: str = "",
        *,
        provider: str | None = None,
        project: str | None = None,
        include_archived: bool = True,
    ) -> tuple[SessionSummary, ...]:
        """Search bounded session projections across projects and providers."""

    async def preview_session(
        self, session_id: str, *, transcript_query: str = ""
    ) -> SessionPreview:
        """Preview one session without implying filesystem restoration."""

    async def rename_session(
        self, binding: SessionActionBinding, title: str
    ) -> SessionSummary:
        """Rename one exact session revision."""

    async def archive_session(
        self, binding: SessionActionBinding, *, archived: bool = True
    ) -> SessionSummary:
        """Archive or restore one exact session revision."""

    async def delete_session(self, binding: SessionActionBinding) -> None:
        """Delete one exact session revision after confirmation."""

    async def fork_session(self, binding: SessionActionBinding) -> SessionSummary:
        """Fork one exact Harness session without claiming native resume."""

    async def export_session(self, binding: SessionActionBinding) -> SessionExport:
        """Create a sanitized local transcript export."""

    async def submit_turn(
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
        capability: str | None = None,
        execution_transport: str | None = None,
        native_session_id: str | None = None,
        native_session_operation: str | None = None,
    ) -> RunSnapshot:
        """Submit one typed turn without invoking the Harness CLI."""

    async def snapshot_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> RunSnapshot:
        """Read a bounded authoritative incremental run snapshot."""

    def stream_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> AsyncIterator[RunSnapshot]:
        """Deliver active run changes with bounded heartbeat resnapshots."""

    async def latest_run(self, session_id: str) -> RunSnapshot | None:
        """Return the newest retained run for session reconnect, if any."""

    async def cancel_run(self, binding: RunActionBinding) -> RunSnapshot:
        """Request cancellation for the exact presented run generation."""

    async def fork_run(self, binding: RunActionBinding) -> SessionSummary:
        """Fork the exact presented run into a new Harness session."""

    async def decide_approval(
        self,
        binding: RunActionBinding,
        approval_id: str,
        decision: str,
    ) -> RunSnapshot:
        """Decide one pending approval bound to the presented run."""

    async def steer_run(
        self,
        binding: RunActionBinding,
        content: str,
        *,
        idempotency_key: str,
    ) -> RunSnapshot:
        """Steer the exact active structured turn where supported."""

    async def answer_input(
        self,
        binding: RunActionBinding,
        input_id: str,
        answer: str,
    ) -> RunSnapshot:
        """Answer one exact provider input request where supported."""

    async def search_files(
        self, session_id: str, query: str
    ) -> tuple[FileCandidate, ...]:
        """Return bounded safe project files with neutralized previews."""

    async def attach_file(self, session_id: str, path: str) -> AttachmentSummary:
        """Create one backend-owned workspace attachment reference."""

    async def inspect_run(self, run_id: str) -> RunInspection:
        """Return bounded diff, evidence, provider, and recovery state."""

    async def provider_handoff(self, session_id: str) -> HandoffPreview:
        """Preview the exact provider-owned target without launching it."""

    async def web_handoff(self, session_id: str) -> HandoffPreview:
        """Preview the exact Web target without silently starting a server."""

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
        """Start native-terminal execution through an application authority."""

    async def snapshot_native_terminal(
        self, process_id: str, *, cursor: int = 0
    ) -> NativeTerminalSnapshot:
        """Read bounded native output after an exact cursor."""

    def stream_native_terminal(
        self,
        process_id: str,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[NativeTerminalSnapshot]:
        """Deliver native output from one persistent bounded stream."""

    async def status_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        """Read authoritative native-process lifecycle state."""

    async def send_native_terminal_input(
        self, process_id: str, data: str, *, submit: bool = False
    ) -> NativeTerminalSnapshot:
        """Send reviewed text input to an exact native process."""

    async def resize_native_terminal(
        self, process_id: str, *, rows: int, columns: int
    ) -> NativeTerminalSnapshot:
        """Resize an exact application-owned native process."""

    async def stop_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        """Stop an exact application-owned native process."""
