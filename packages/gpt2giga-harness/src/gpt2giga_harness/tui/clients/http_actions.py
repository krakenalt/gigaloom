"""HTTP attach application and environment action adapter methods."""

from __future__ import annotations

from typing import Any, AsyncIterator, Mapping
from urllib.parse import urlencode


from gpt2giga_harness.workbench_protocol import (
    WorkbenchStatePage,
    workbench_state_page_from_dict,
)
from gpt2giga_harness.workbench_resources import (
    PreferenceSnapshot,
    ProcessProjection,
    TaskProjection,
    WorkbenchResourceSnapshot,
    preference_snapshot_from_dict,
    process_binding,
    resource_snapshot_from_dict,
    task_binding,
)

from gpt2giga_harness.tui.contracts import (
    MAX_FILE_CANDIDATES,
    MAX_DIFF_PREVIEW_CHARS,
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

from gpt2giga_harness.tui.projections.values import (
    _mapping,
    _mapping_items,
    _required_text,
    _required_identity,
    _path_identity,
    _required_content,
)


from gpt2giga_harness.tui.projections.environment import (
    _environment_commit_preview_summary,
    _environment_commit_apply_summary,
    _environment_push_preview_summary,
    _environment_push_apply_summary,
    _environment_pull_request_preview_summary,
    _environment_pull_request_apply_summary,
)

from gpt2giga_harness.tui.projections.resources import (
    _attachment_ids,
    _file_candidate_from_mapping,
    _attachment_summary,
    _provider_handoff_from_mapping,
    _blocked_provider_handoff,
)

from gpt2giga_harness.tui.projections.run_inspection import _attached_run_inspection


from gpt2giga_harness.tui.projections.native import (
    _native_terminal_snapshot_from_mapping,
    _native_terminal_input,
    _native_terminal_dimensions,
    _non_negative_cursor,
)


class _AttachedActionsMixin:
    """Cohesive non-run actions for the HTTP attach transport."""

    async def workbench_state(
        self,
        *,
        cursor: str | None = None,
        limit: int = 32,
    ) -> WorkbenchStatePage:
        """Read provider-neutral projections through the authenticated API."""
        query: dict[str, str | int] = {"limit": min(max(limit, 1), 32)}
        if cursor is not None:
            query["cursor"] = cursor
        response = await self._request(
            "GET", f"/api/workbench/state?{urlencode(query)}"
        )
        return workbench_state_page_from_dict(response)

    async def resources(
        self, session_id: str | None = None
    ) -> WorkbenchResourceSnapshot:
        """Read bounded resources through the authenticated API."""
        query = f"?{urlencode({'session_id': session_id})}" if session_id else ""
        response = await self._request("GET", f"/api/workbench/resources{query}")
        return resource_snapshot_from_dict(response)

    async def cancel_task(self, task: TaskProjection) -> TaskProjection:
        """Request exact task cancellation through application authority."""
        response = await self._request(
            "POST",
            f"/api/workbench/tasks/{_path_identity(task.id)}/cancel",
            {"binding": task_binding(task)},
        )
        return TaskProjection(**_mapping(response.get("task")))

    async def stop_process(self, process: ProcessProjection) -> ProcessProjection:
        """Stop one exact native process through its server-side owner."""
        response = await self._request(
            "POST",
            f"/api/workbench/processes/{_path_identity(process.id)}/stop",
            {"binding": process_binding(process)},
        )
        return ProcessProjection(**_mapping(response.get("process")))

    async def save_preferences(
        self, values: Mapping[str, Any], *, expected_revision: str
    ) -> PreferenceSnapshot:
        """Persist private Workbench-only preferences through attach mode."""
        response = await self._request(
            "PUT",
            "/api/workbench/preferences",
            {"values": dict(values), "expected_revision": expected_revision},
        )
        return preference_snapshot_from_dict(_mapping(response.get("preferences")))

    async def preview_environment_commit(
        self,
        workspace: str,
        *,
        message: str,
        author_name: str,
        author_email: str,
    ) -> EnvironmentCommitPreviewSummary:
        response = await self._request(
            "POST",
            "/api/environment/commit/preview",
            {
                "workspace": workspace,
                "message": message,
                "author_name": author_name,
                "author_email": author_email,
            },
        )
        return _environment_commit_preview_summary(_mapping(response.get("preview")))

    async def apply_environment_commit(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentCommitApplySummary:
        payload: dict[str, Any] = {
            "preview_id": preview_id,
            "workspace": workspace,
        }
        if session_id is not None:
            payload.pop("workspace")
            payload["session_id"] = session_id
        response = await self._request("POST", "/api/environment/commit/apply", payload)
        return _environment_commit_apply_summary(response)

    async def decide_environment_approval(
        self, approval_id: str, decision: str
    ) -> None:
        await self._request(
            "POST",
            f"/api/approvals/{_path_identity(approval_id)}/decision",
            {"decision": decision},
        )

    async def preview_environment_push(
        self, workspace: str
    ) -> EnvironmentPushPreviewSummary:
        response = await self._request(
            "POST",
            "/api/environment/push/preview",
            {"workspace": workspace},
        )
        return _environment_push_preview_summary(_mapping(response.get("preview")))

    async def apply_environment_push(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPushApplySummary:
        payload: dict[str, Any] = {
            "preview_id": preview_id,
            "workspace": workspace,
        }
        if session_id is not None:
            payload.pop("workspace")
            payload["session_id"] = session_id
        response = await self._request("POST", "/api/environment/push/apply", payload)
        return _environment_push_apply_summary(response)

    async def preview_environment_pull_request(
        self,
        workspace: str,
        *,
        title: str,
        body: str,
        base_branch: str | None = None,
    ) -> EnvironmentPullRequestPreviewSummary:
        payload: dict[str, Any] = {
            "workspace": workspace,
            "title": title,
            "body": body,
        }
        if base_branch is not None:
            payload["base_branch"] = base_branch
        response = await self._request(
            "POST", "/api/environment/pull-request/preview", payload
        )
        return _environment_pull_request_preview_summary(
            _mapping(response.get("preview"))
        )

    async def apply_environment_pull_request(
        self,
        preview_id: str,
        *,
        workspace: str,
        session_id: str | None = None,
    ) -> EnvironmentPullRequestApplySummary:
        payload: dict[str, Any] = {
            "preview_id": preview_id,
            "workspace": workspace,
        }
        if session_id is not None:
            payload.pop("workspace")
            payload["session_id"] = session_id
        response = await self._request(
            "POST", "/api/environment/pull-request/apply", payload
        )
        return _environment_pull_request_apply_summary(response)

    async def search_files(
        self, session_id: str, query: str
    ) -> tuple[FileCandidate, ...]:
        response = await self._request(
            "GET",
            f"/api/sessions/{_path_identity(session_id)}/attachments/workspace/search?"
            + urlencode({"q": query, "limit": MAX_FILE_CANDIDATES}),
        )
        candidates: list[FileCandidate] = []
        for item in _mapping_items(response.get("files"), MAX_FILE_CANDIDATES):
            path = _required_text(item.get("path"), "file path")
            preview_response = await self._request(
                "GET",
                f"/api/sessions/{_path_identity(session_id)}/attachments/workspace/preview?"
                + urlencode({"path": path}),
            )
            candidates.append(_file_candidate_from_mapping(item, preview_response))
        return tuple(candidates)

    async def attach_file(self, session_id: str, path: str) -> AttachmentSummary:
        response = await self._request(
            "POST",
            f"/api/sessions/{_path_identity(session_id)}/attachments/workspace",
            {"path": _required_content(path, "file path")},
        )
        return _attachment_summary(_mapping(response.get("attachment")))

    async def inspect_run(self, run_id: str) -> RunInspection:
        identity = _path_identity(run_id)
        run_payload, diff_payload, harness_payload = await self._parallel_get(
            f"/api/cockpit/runs/{identity}",
            f"/api/cockpit/runs/{identity}/diff?max_bytes={MAX_DIFF_PREVIEW_CHARS + 1024}",
            "/api/harnesses",
        )
        summary_payload = await self._request_optional(
            "GET", f"/api/runs/{identity}/summary"
        )
        return _attached_run_inspection(
            run_payload, diff_payload, summary_payload, harness_payload
        )

    async def provider_handoff(self, session_id: str) -> HandoffPreview:
        response = await self._request(
            "GET", f"/api/sessions/{_path_identity(session_id)}"
        )
        session = _mapping(response.get("session")) or response
        harness_id = _required_identity(session.get("default_harness_id"), "Harness id")
        workspace = _required_text(session.get("workspace"), "session workspace")
        handoff = await self._request_optional(
            "GET",
            f"/api/provider-handoffs/{harness_id}/preview?"
            + urlencode(
                {
                    "action": "open_provider_ui",
                    "workspace": workspace,
                    "launch_mode": "interactive",
                }
            ),
        )
        if not handoff:
            return _blocked_provider_handoff(harness_id)
        return _provider_handoff_from_mapping(
            _mapping(handoff.get("handoff")), harness_id=harness_id
        )

    async def web_handoff(self, session_id: str) -> HandoffPreview:
        identity = _path_identity(session_id)
        await self._request("GET", f"/api/sessions/{identity}")
        return HandoffPreview(
            kind="web",
            status="ready",
            target=f"{self.base_url}/cockpit-v2/work/{identity}",
            continuity=f"The Web client resumes Harness session {identity}.",
            observability=(
                "Web and TUI share the server application/runtime/store authority",
                "browser authentication and rendering remain Web-owned",
            ),
            instruction="Open this local URL after reviewing the target and boundary.",
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
        payload: dict[str, Any] = {
            "action": "start",
            "session_id": _path_identity(session_id),
            "prompt": _required_content(content, "native terminal prompt"),
            "idempotency_key": _required_identity(idempotency_key, "idempotency key"),
            "attachment_ids": list(_attachment_ids(attachment_ids)),
            "execution_transport": "native_terminal",
            "invocation_mode": "native",
        }
        payload.update(
            {
                key: value
                for key, value in {
                    "harness_id": harness_id,
                    "model": model,
                    "api_mode": api_mode,
                    "mode": mode,
                }.items()
                if value is not None
            }
        )
        response = await self._request(
            "POST",
            "/api/native/processes/start",
            payload,
        )
        if bool(response.get("approval_required")):
            raise WorkbenchClientError(
                "native process approval is required; review the retained approval "
                "before retrying this exact start"
            )
        return _native_terminal_snapshot_from_mapping(response)

    async def snapshot_native_terminal(
        self, process_id: str, *, cursor: int = 0
    ) -> NativeTerminalSnapshot:
        validated_cursor = _non_negative_cursor(cursor)
        response = await self._request(
            "GET",
            f"/api/native/processes/{_path_identity(process_id)}/output?"
            + urlencode({"cursor": validated_cursor}),
        )
        return _native_terminal_snapshot_from_mapping(response)

    async def stream_native_terminal(
        self,
        process_id: str,
        *,
        cursor: int = 0,
    ) -> AsyncIterator[NativeTerminalSnapshot]:
        """Follow the existing native-output SSE endpoint without active polling."""
        validated_process_id = _path_identity(process_id)
        validated_cursor = _non_negative_cursor(cursor)
        query = urlencode({"cursor": validated_cursor})
        async for frame in self._stream_sse(
            f"/api/native/processes/{validated_process_id}/output/stream?{query}"
        ):
            snapshot = _native_terminal_snapshot_from_mapping(frame.data)
            if snapshot.process_id != process_id:
                raise WorkbenchClientError("native stream identity changed")
            yield snapshot
            if snapshot.terminal:
                return

    async def status_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        response = await self._request(
            "GET", f"/api/native/processes/{_path_identity(process_id)}"
        )
        return _native_terminal_snapshot_from_mapping(response)

    async def send_native_terminal_input(
        self, process_id: str, data: str, *, submit: bool = False
    ) -> NativeTerminalSnapshot:
        response = await self._request(
            "POST",
            f"/api/native/processes/{_path_identity(process_id)}/input",
            {"data": _native_terminal_input(data), "submit": bool(submit)},
        )
        return _native_terminal_snapshot_from_mapping(response)

    async def resize_native_terminal(
        self, process_id: str, *, rows: int, columns: int
    ) -> NativeTerminalSnapshot:
        validated_rows, validated_columns = _native_terminal_dimensions(rows, columns)
        response = await self._request(
            "POST",
            f"/api/native/processes/{_path_identity(process_id)}/resize",
            {"rows": validated_rows, "columns": validated_columns},
        )
        return _native_terminal_snapshot_from_mapping(response)

    async def stop_native_terminal(self, process_id: str) -> NativeTerminalSnapshot:
        response = await self._request(
            "DELETE", f"/api/native/processes/{_path_identity(process_id)}"
        )
        return _native_terminal_snapshot_from_mapping(response)
