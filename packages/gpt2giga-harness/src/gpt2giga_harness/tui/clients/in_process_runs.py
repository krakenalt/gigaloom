"""In-process run lifecycle adapter methods."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
from typing import Any, AsyncIterator

import anyio

from gpt2giga_harness.runtime.models import ApprovalStatus
from gpt2giga_harness.runtime.policy import approval_request_to_dict
from gpt2giga_harness.sessions.models import (
    HarnessRun,
)
from gpt2giga_harness.sessions.event_stream import (
    StreamCapacityError,
    StreamSignal,
)
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.structured_sessions import StructuredTurnInput

from gpt2giga_harness.tui.contracts import (
    MAX_RESPONSE_BYTES,
    MAX_TIMELINE_EVENTS,
    RUN_START_TIMEOUT_SECONDS,
    RUN_STREAM_RESNAPSHOT_SECONDS,
    WorkbenchClientError,
    SessionSummary,
    RunActionBinding,
    RunSnapshot,
)

from gpt2giga_harness.tui.projections.values import (
    _required_identity,
    _required_content,
)

from gpt2giga_harness.tui.projections.navigation import _session_summary


from gpt2giga_harness.tui.projections.resources import _attachment_ids


from gpt2giga_harness.tui.projections.runs import (
    _run_revision,
    _run_generation,
    _run_idempotency_key,
    _run_snapshot,
    _parse_in_process_cursor,
    _bounded_timeline,
    _approval_summary,
    _messages_through_run,
)
from gpt2giga_harness.tui.clients.session_queries import (
    newest_session_run_id,
    preferred_session_run,
)


class _InProcessRunsMixin:
    """Run lifecycle operations for the in-process transport."""

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
        prompt = _required_content(content, "turn content")
        key = _required_identity(idempotency_key, "idempotency key")
        previous_run_id = self._submitted_turns.get(key)
        if previous_run_id is not None:
            previous = self.sessions.get_run(previous_run_id)
            if previous.session_id != session_id:
                raise WorkbenchClientError("idempotency key belongs to another session")
            return await self.snapshot_run(previous.id)
        before_run_id = newest_session_run_id(self.store, session_id)
        cancel_event = threading.Event()
        payload: dict[str, Any] = {
            "prompt": prompt,
            "stream": True,
            "attachment_ids": list(_attachment_ids(attachment_ids)),
        }
        payload.update(
            {
                key: value
                for key, value in {
                    "harness_id": harness_id,
                    "model": model,
                    "api_mode": api_mode,
                    "mode": mode,
                    "capability": capability,
                    "execution_transport": execution_transport,
                    "native_session_id": native_session_id,
                }.items()
                if value is not None
            }
        )
        if native_session_operation is not None:
            payload["extra"] = {"native_session_operation": native_session_operation}
        task = asyncio.create_task(
            anyio.to_thread.run_sync(
                lambda: self.sessions.run_turn(
                    session_id,
                    payload,
                    cancel_event=cancel_event,
                ),
                abandon_on_cancel=True,
            ),
            name=f"tui-turn-{key}",
        )
        run = await self._wait_for_run(session_id, before_run_id, task)
        self._submitted_turns[key] = run.id
        self._active_runs[run.id] = (task, cancel_event)
        task.add_done_callback(
            lambda done, run_id=run.id: self._finish_run(run_id, done)
        )
        return await self.snapshot_run(run.id)

    async def snapshot_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> RunSnapshot:
        run = self.sessions.get_run(run_id)
        generation = _run_generation(run)
        offset, cursor_generation, cursor_invalid = _parse_in_process_cursor(cursor)
        reason = None
        if cursor_invalid:
            offset = 0
            reason = "cursor_gap"
        elif cursor_generation is not None and cursor_generation != generation:
            offset = 0
            reason = "generation_changed"
        try:
            current, page = self.sessions.read_run_event_tail(
                run_id,
                offset,
                limit=MAX_TIMELINE_EVENTS,
                max_bytes=MAX_RESPONSE_BYTES,
            )
        except ValueError:
            current, page = self.sessions.read_run_event_tail(
                run_id,
                0,
                limit=MAX_TIMELINE_EVENTS,
                max_bytes=MAX_RESPONSE_BYTES,
            )
            reason = "cursor_gap"
        if page.has_more:
            reason = reason or "slow_consumer"
        events = _bounded_timeline(tuple(item.event for item in page.items))
        job = self.sessions.find_job_for_run(run_id)
        approvals = tuple(
            _approval_summary(approval_request_to_dict(item))
            for item in self.runtime_store.list_run_approval_requests(
                run_id=run_id,
                job_id=job.id if job is not None else None,
                limit=20,
            )
            if item.status is ApprovalStatus.PENDING
        )
        return _run_snapshot(
            current,
            events=events,
            cursor=f"ip1.{generation}.{page.next_offset}",
            idempotency_key=_run_idempotency_key(current, self._submitted_turns),
            pending_approvals=approvals,
            resnapshot_reason=reason,
        )

    async def stream_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> AsyncIterator[RunSnapshot]:
        """Use the store broker plus a four-per-minute durability heartbeat."""
        try:
            subscription = self.store.event_broker.subscribe(run_id)
        except StreamCapacityError as exc:
            raise WorkbenchClientError(str(exc)) from exc
        current_cursor = cursor
        first = True
        try:
            while True:
                signal = (
                    StreamSignal.CHANGED
                    if first
                    else await subscription.wait(RUN_STREAM_RESNAPSHOT_SECONDS)
                )
                first = False
                snapshot = await self.snapshot_run(
                    run_id,
                    cursor=(
                        None
                        if signal is StreamSignal.RESNAPSHOT_REQUIRED
                        else current_cursor
                    ),
                )
                current_cursor = snapshot.cursor
                yield snapshot
                if snapshot.terminal:
                    return
        finally:
            subscription.close()

    async def latest_run(self, session_id: str) -> RunSnapshot | None:
        run = preferred_session_run(self.store, session_id)
        if run is None:
            return None
        return await self.snapshot_run(run.id)

    async def cancel_run(self, binding: RunActionBinding) -> RunSnapshot:
        run = self._validate_binding(binding)
        active = self._active_runs.get(run.id)
        if active is not None:
            active[1].set()
        else:
            job = self.sessions.find_job_for_run(run.id)
            if job is None:
                raise WorkbenchClientError(
                    "run owner is unavailable; authoritative resnapshot required"
                )
            self.runtime_store.request_cancel(job.id)
        return await self.snapshot_run(run.id)

    async def fork_run(self, binding: RunActionBinding) -> SessionSummary:
        run = self._validate_binding(binding)
        source = self.store.get_session(run.session_id)
        metadata = {
            **dict(source.metadata),
            "forked_from_session_id": source.id,
            "forked_from_run_id": run.id,
        }
        metadata.pop("structured_session_link", None)
        fork = self.store.create_session(
            title=f"Fork: {source.title}",
            workspace=run.workspace or source.workspace,
            default_harness_id=run.harness_id,
            default_model=run.model,
            default_api_mode=run.api_mode,
            default_mode=run.mode,
            metadata=metadata,
        )
        for message in _messages_through_run(
            self.store.list_messages(source.id), run.id
        ):
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
                        "forked_from_run_id": run.id,
                    },
                )
            )
        return _session_summary(fork)

    async def decide_approval(
        self,
        binding: RunActionBinding,
        approval_id: str,
        decision: str,
    ) -> RunSnapshot:
        run = self._validate_binding(binding)
        approval = self.runtime_store.get_approval_request(approval_id)
        if approval.run_id != run.id:
            raise WorkbenchClientError("approval does not belong to the selected run")
        self.sessions.decide_approval(approval_id, decision)
        return await self.snapshot_run(run.id)

    async def steer_run(
        self,
        binding: RunActionBinding,
        content: str,
        *,
        idempotency_key: str,
    ) -> RunSnapshot:
        run = self._validate_binding(binding)
        text = _required_content(content, "steer content")
        input_key = _required_identity(idempotency_key, "idempotency key")
        harness = self.registry.get(run.harness_id)
        supervisors = getattr(harness, "_app_server_supervisors", {})
        supervisor = getattr(harness, "app_server_supervisor", None) or supervisors.get(
            self.config.data_dir
        )
        if supervisor is None:
            raise WorkbenchClientError(
                "active structured owner is unavailable; resnapshot before retry"
            )
        try:
            supervisor.steer_turn(run.session_id, StructuredTurnInput(input_key, text))
        except (RuntimeError, ValueError) as exc:
            raise WorkbenchClientError(str(exc)) from exc
        return await self.snapshot_run(run.id)

    async def answer_input(
        self,
        binding: RunActionBinding,
        input_id: str,
        answer: str,
    ) -> RunSnapshot:
        self._validate_binding(binding)
        _required_identity(input_id, "input request")
        _required_content(answer, "input answer")
        raise WorkbenchClientError(
            "the selected provider does not advertise interactive input"
        )

    async def _wait_for_run(
        self,
        session_id: str,
        before_run_id: str | None,
        task: asyncio.Task[Any],
    ) -> HarnessRun:
        deadline = asyncio.get_running_loop().time() + RUN_START_TIMEOUT_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            current_run_id = newest_session_run_id(self.store, session_id)
            if current_run_id is not None and current_run_id != before_run_id:
                return self.sessions.get_run(current_run_id)
            if task.done():
                task.result()
                break
            await asyncio.sleep(0.01)
        task.cancel()
        raise WorkbenchClientError("turn did not create a run before the timeout")

    def _finish_run(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._active_runs.pop(run_id, None)
        if not task.cancelled():
            task.exception()

    def _validate_binding(self, binding: RunActionBinding) -> HarnessRun:
        run = self.sessions.get_run(binding.run_id)
        if run.session_id != binding.session_id:
            raise WorkbenchClientError("run identity changed; resnapshot required")
        if _run_revision(run) != binding.revision:
            raise WorkbenchClientError("run revision changed; resnapshot required")
        if _run_generation(run) != binding.generation:
            raise WorkbenchClientError("run generation changed; resnapshot required")
        return run
