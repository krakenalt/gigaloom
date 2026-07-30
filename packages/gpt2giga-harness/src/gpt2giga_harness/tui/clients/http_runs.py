"""HTTP attach run lifecycle and SSE adapter methods."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, AsyncIterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    Request,
)

import anyio


from gpt2giga_harness.tui.contracts import (
    MAX_RESPONSE_BYTES,
    MAX_TIMELINE_EVENTS,
    MAX_SSE_LINE_BYTES,
    _TERMINAL_RUN_STATUSES,
    WorkbenchClientError,
    SessionSummary,
    RunActionBinding,
    RunSnapshot,
)

from gpt2giga_harness.tui.projections.values import (
    _mapping,
    _mapping_items,
    _required_text,
    _optional_text,
    _display_text,
    _optional_display_text,
    _required_identity,
    _path_identity,
    _required_content,
)

from gpt2giga_harness.tui.projections.navigation import _session_summary_from_mapping


from gpt2giga_harness.tui.projections.resources import _attachment_ids


from gpt2giga_harness.tui.projections.runs import (
    _mapping_revision,
    _mapping_generation,
    _parse_attach_cursor,
    _bounded_timeline,
    _timeline_event,
    _event_from_mapping,
    _approval_summary,
    _binding_payload,
)


from gpt2giga_harness.tui.clients.sse import (
    _SseFrame,
    _decode_sse_frame,
    _event_requires_run_resnapshot,
)


class _AttachedRunsMixin:
    """Run lifecycle operations for the HTTP attach transport."""

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
        payload: dict[str, Any] = {
            "prompt": _required_content(content, "turn content"),
            "stream": True,
            "idempotency_key": _required_identity(idempotency_key, "idempotency key"),
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
        response = await self._request(
            "POST",
            f"/api/sessions/{_path_identity(session_id)}/run/start",
            payload,
        )
        run_id = _required_identity(_mapping(response.get("run")).get("id"), "run id")
        return await self.snapshot_run(run_id)

    async def snapshot_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> RunSnapshot:
        run_payload = await self._request(
            "GET", f"/api/cockpit/runs/{_path_identity(run_id)}"
        )
        run = _mapping(run_payload.get("run"))
        session_id = _required_identity(run.get("session_id"), "session id")
        generation = _mapping_generation(_mapping(run.get("provider_session")))
        cursor_event_id, cursor_generation, cursor_invalid = _parse_attach_cursor(
            cursor
        )
        reason = None
        if cursor_invalid:
            cursor_event_id = None
            reason = "cursor_gap"
        elif cursor_generation is not None and cursor_generation != generation:
            cursor_event_id = None
            reason = "generation_changed"
        query_values = {"run_id": run_id}
        if cursor_event_id:
            query_values["after_id"] = cursor_event_id
        events_payload, approvals_payload = await self._parallel_get(
            f"/api/sessions/{session_id}/events?{urlencode(query_values)}",
            "/api/approvals?status=pending&limit=100",
        )
        raw_events = _mapping_items(
            events_payload.get("events"), MAX_TIMELINE_EVENTS + 1
        )
        if len(raw_events) > MAX_TIMELINE_EVENTS:
            raw_events = raw_events[-MAX_TIMELINE_EVENTS:]
            reason = reason or "slow_consumer"
        if (
            cursor_event_id
            and not raw_events
            and str(run.get("status")) not in _TERMINAL_RUN_STATUSES
        ):
            full_payload = await self._request(
                "GET",
                f"/api/sessions/{session_id}/events?{urlencode({'run_id': run_id})}",
            )
            full = _mapping_items(full_payload.get("events"), MAX_TIMELINE_EVENTS + 1)
            known_ids = {str(item.get("id")) for item in full}
            if cursor_event_id not in known_ids:
                raw_events = full[-MAX_TIMELINE_EVENTS:]
                reason = "cursor_gap"
        events = _bounded_timeline(
            tuple(_event_from_mapping(item) for item in raw_events)
        )
        last_event_id = events[-1].id if events else cursor_event_id
        approvals = tuple(
            _approval_summary(item)
            for item in _mapping_items(approvals_payload.get("approvals"), 100)
            if str(item.get("run_id") or "") == run_id
        )
        revision = _required_text(
            run_payload.get("snapshot_revision") or _mapping_revision(run),
            "run revision",
        )
        binding = RunActionBinding(
            session_id=session_id,
            run_id=run_id,
            revision=revision,
            generation=generation,
            idempotency_key=_required_identity(
                _mapping(run.get("runtime")).get("idempotency_key") or f"run-{run_id}",
                "idempotency key",
            ),
        )
        return RunSnapshot(
            binding=binding,
            status=_display_text(run.get("status") or "unknown"),
            events=events,
            cursor=(
                f"at1.{generation}.{last_event_id}"
                if last_event_id is not None
                else None
            ),
            pending_approvals=approvals,
            resnapshot_reason=reason,
            execution_transport=_optional_display_text(run.get("execution_transport")),
            native_process_id=_optional_text(run.get("native_process_id")),
        )

    async def stream_run(
        self,
        run_id: str,
        *,
        cursor: str | None = None,
    ) -> AsyncIterator[RunSnapshot]:
        """Follow the existing durable SSE endpoint and resnapshot on delivery."""
        validated_run_id = _path_identity(run_id)
        event_id, _generation, cursor_invalid = _parse_attach_cursor(cursor)
        query = (
            urlencode({"tail_only": "true"})
            if event_id is None or cursor_invalid
            else urlencode({"after_id": event_id})
        )
        current_cursor = None if cursor_invalid else cursor
        current_snapshot: RunSnapshot | None = None
        snapshot_event_ids: set[str] = set()
        async for frame in self._stream_sse(
            f"/api/runs/{validated_run_id}/events/stream?{query}"
        ):
            if frame.event != "resnapshot" and (
                _optional_text(frame.data.get("run_id")) != run_id
            ):
                raise WorkbenchClientError("run stream identity changed")
            if current_snapshot is None:
                current_snapshot = await self.snapshot_run(
                    run_id,
                    cursor=current_cursor,
                )
                current_cursor = current_snapshot.cursor
                snapshot_event_ids = {event.id for event in current_snapshot.events}
                yield current_snapshot
                if current_snapshot.terminal:
                    return
            if frame.event == "resnapshot" or _event_requires_run_resnapshot(
                frame.data
            ):
                current_snapshot = await self.snapshot_run(
                    run_id,
                    cursor=None if frame.event == "resnapshot" else current_cursor,
                )
                current_cursor = current_snapshot.cursor
                snapshot_event_ids = {event.id for event in current_snapshot.events}
                yield current_snapshot
                if current_snapshot.terminal:
                    return
                continue
            raw_event = _event_from_mapping(frame.data)
            if raw_event.id in snapshot_event_ids:
                continue
            event = _timeline_event(raw_event)
            current_cursor = f"at1.{current_snapshot.binding.generation}.{event.id}"
            current_snapshot = replace(
                current_snapshot,
                events=(event,),
                cursor=current_cursor,
                resnapshot_reason=None,
            )
            snapshot_event_ids = {event.id}
            yield current_snapshot

    async def latest_run(self, session_id: str) -> RunSnapshot | None:
        response = await self._request(
            "GET", f"/api/sessions/{_path_identity(session_id)}"
        )
        runs = _mapping_items(response.get("runs"), 100)
        if not runs:
            return None
        active = [
            run
            for run in runs
            if str(run.get("status") or "") not in _TERMINAL_RUN_STATUSES
        ]
        selected = (active or list(runs))[-1]
        return await self.snapshot_run(_required_identity(selected.get("id"), "run id"))

    async def cancel_run(self, binding: RunActionBinding) -> RunSnapshot:
        await self._validate_binding(binding)
        await self._request(
            "POST",
            f"/api/runs/{binding.run_id}/cancel",
            _binding_payload(binding),
        )
        return await self.snapshot_run(binding.run_id)

    async def fork_run(self, binding: RunActionBinding) -> SessionSummary:
        await self._validate_binding(binding)
        response = await self._request(
            "POST", f"/api/runs/{binding.run_id}/fork", _binding_payload(binding)
        )
        return _session_summary_from_mapping(_mapping(response.get("session")))

    async def decide_approval(
        self,
        binding: RunActionBinding,
        approval_id: str,
        decision: str,
    ) -> RunSnapshot:
        await self._validate_binding(binding)
        await self._request(
            "POST",
            f"/api/approvals/{_path_identity(approval_id)}/decision",
            {"decision": decision, "run_binding": _binding_payload(binding)},
        )
        return await self.snapshot_run(binding.run_id)

    async def steer_run(
        self,
        binding: RunActionBinding,
        content: str,
        *,
        idempotency_key: str,
    ) -> RunSnapshot:
        await self._validate_binding(binding)
        await self._request(
            "POST",
            f"/api/runs/{binding.run_id}/steer",
            {
                **_binding_payload(binding),
                "content": _required_content(content, "steer content"),
                "idempotency_key": _required_identity(
                    idempotency_key, "idempotency key"
                ),
            },
        )
        return await self.snapshot_run(binding.run_id)

    async def answer_input(
        self,
        binding: RunActionBinding,
        input_id: str,
        answer: str,
    ) -> RunSnapshot:
        await self._validate_binding(binding)
        await self._request(
            "POST",
            f"/api/runs/{binding.run_id}/input",
            {
                **_binding_payload(binding),
                "input_id": _required_identity(input_id, "input request"),
                "answer": _required_content(answer, "input answer"),
            },
        )
        return await self.snapshot_run(binding.run_id)

    async def _validate_binding(self, binding: RunActionBinding) -> None:
        current = await self.snapshot_run(binding.run_id)
        if (
            current.binding.session_id != binding.session_id
            or current.binding.revision != binding.revision
            or current.binding.generation != binding.generation
        ):
            raise WorkbenchClientError("run changed; authoritative resnapshot required")

    async def _stream_sse(self, path: str) -> AsyncIterator[_SseFrame]:
        await self._ensure_session()

        def open_stream():
            request = Request(
                f"{self.base_url}{path}",
                headers={"Accept": "text/event-stream"},
                method="GET",
            )
            try:
                return self._opener.open(
                    request,
                    timeout=max(self.timeout_seconds, 30.0),
                )
            except HTTPError as exc:
                raise WorkbenchClientError(
                    f"attach stream rejected ({exc.code})"
                ) from exc
            except (OSError, URLError) as exc:
                raise WorkbenchClientError("attach stream is unavailable") from exc

        response = await anyio.to_thread.run_sync(
            open_stream,
            abandon_on_cancel=True,
        )
        content_type = str(response.headers.get("Content-Type") or "")
        if not content_type.lower().startswith("text/event-stream"):
            response.close()
            raise WorkbenchClientError("attach stream returned an invalid content type")
        event_name = "message"
        event_id: str | None = None
        data_lines: list[str] = []
        frame_bytes = 0
        try:
            while True:
                try:
                    raw_line = await anyio.to_thread.run_sync(
                        lambda: response.readline(MAX_SSE_LINE_BYTES + 1),
                        abandon_on_cancel=True,
                    )
                except (OSError, TimeoutError) as exc:
                    raise WorkbenchClientError("attach stream was interrupted") from exc
                if not raw_line:
                    break
                if len(raw_line) > MAX_SSE_LINE_BYTES:
                    raise WorkbenchClientError("attach stream line exceeded the limit")
                frame_bytes += len(raw_line)
                if frame_bytes > MAX_RESPONSE_BYTES:
                    raise WorkbenchClientError("attach stream frame exceeded the limit")
                try:
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                except UnicodeDecodeError as exc:
                    raise WorkbenchClientError(
                        "attach stream is not valid UTF-8"
                    ) from exc
                if not line:
                    if data_lines:
                        yield _decode_sse_frame(event_name, event_id, data_lines)
                    event_name = "message"
                    event_id = None
                    data_lines = []
                    frame_bytes = 0
                    continue
                if line.startswith(":"):
                    continue
                field, separator, value = line.partition(":")
                value = value.lstrip(" ") if separator else ""
                if field == "event":
                    event_name = value or "message"
                elif field == "id":
                    event_id = value[:512] or None
                elif field == "data":
                    data_lines.append(value)
            if data_lines:
                yield _decode_sse_frame(event_name, event_id, data_lines)
        finally:
            response.close()
