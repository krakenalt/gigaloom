"""Codex app-server structured-session driver."""

from __future__ import annotations

from dataclasses import replace
import threading
from typing import TYPE_CHECKING, Any, Callable, Mapping

from gigaloom.execution import (
    ExecutionSnapshot,
)
from gigaloom.harnesses.ports import ApprovalDecision, utc_now
from gigaloom.structured_sessions import (
    AdapterCapabilitySnapshot,
    StructuredSessionError,
    StructuredSessionLink,
    StructuredSessionState,
    StructuredTurnInput,
    StructuredTurnResult,
    UnsupportedSessionCapability,
)
from gigaloom.types import (
    HarnessContext,
    HarnessEvent,
    HarnessEventType,
    HarnessRequest,
    HarnessResult,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_DRIVER_PROTOCOL_VERSION,
    APP_SERVER_LINK_SCHEMA_VERSION,
    APP_SERVER_PROTOCOL,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerProtocolError,
    _APPROVAL_METHODS,
    _PendingApproval,
    _Runtime,
)
from gigaloom.harnesses.builtins.codex.app_server.protocol import (
    _approval_response,
    _decline_server_request,
    _matching_turn_status,
    _normalize_provider_decision,
    _thread_identity_params,
)
from gigaloom.harnesses.builtins.codex.app_server.utils import (
    _mapping,
    _optional_text,
    _publish,
    _thread_id,
    _turn_id,
)

if TYPE_CHECKING:
    from gigaloom.harnesses.builtins.codex.app_server.session import (
        CodexAppServerSupervisor,
    )


class CodexAppServerDriver:
    """Adapt Codex app-server thread/turn continuity to the generic driver."""

    def __init__(
        self,
        *,
        supervisor: CodexAppServerSupervisor,
        runtime: _Runtime,
        request: HarnessRequest,
        context: HarnessContext,
        command_display: tuple[str, ...],
        continuation: Mapping[str, Any],
        legacy_snapshot: Mapping[str, Any],
        legacy_link: Mapping[str, Any] | None,
        adapter_version: str,
    ) -> None:
        self.supervisor = supervisor
        self.runtime = runtime
        self.request = request
        self.context = context
        self.command_display = command_display
        self.continuation = dict(continuation)
        self.legacy_snapshot = dict(legacy_snapshot)
        self.legacy_link = dict(legacy_link) if legacy_link is not None else None
        self.adapter_version = adapter_version
        self.thread_id: str | None = None
        self.active_turn_id: str | None = None
        self.result: HarnessResult | None = None
        self._approval_bridge: Callable[[Mapping[str, Any]], str] | None = None
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._pending_approval_lock = threading.Lock()

    def probe(self) -> AdapterCapabilitySnapshot:
        """Project reviewed app-server v2 behavior into neutral capabilities."""
        return AdapterCapabilitySnapshot(
            adapter_id="codex-cli",
            adapter_version=self.adapter_version,
            protocol=APP_SERVER_PROTOCOL,
            protocol_version=APP_SERVER_DRIVER_PROTOCOL_VERSION,
            structured_events=True,
            partial_output=True,
            interactive_input=False,
            live_approvals=True,
            durable_approval=True,
            interrupt=True,
            steer=True,
            resume=True,
            fork=True,
            session_list=False,
            session_close=False,
            native_auth=False,
            provider_ui_handoff=False,
            dynamic_model=False,
            dynamic_mcp=False,
            recovery_after_process_loss=True,
        )

    def open_or_resume(
        self,
        execution_snapshot: ExecutionSnapshot,
        session_link: StructuredSessionLink | None,
    ) -> StructuredSessionState:
        """Start, import, resume, or explicitly fork one provider thread."""
        del execution_snapshot
        action = str(self.continuation.get("action") or "start")
        recovery_outcome = "not_required"
        forked_from: str | None = None
        if action == "fork":
            source_thread_id = str(
                self.continuation.get("fork_thread_id")
                or _mapping(self.continuation.get("fork")).get("thread_id")
                or ""
            ).strip()
            if not source_thread_id:
                raise ValueError("Codex app-server fork requires a source thread id")
            response = self.runtime.client.request(
                "thread/fork",
                {
                    **_thread_identity_params(self.request),
                    "threadId": source_thread_id,
                    "lastTurnId": self.continuation.get("fork_turn_id"),
                },
                timeout=APP_SERVER_TIMEOUT_SECONDS,
            )
            thread_id = _thread_id(response)
            forked_from = source_thread_id
            recovery_outcome = "forked"
        else:
            legacy_thread_id = _optional_text((self.legacy_link or {}).get("thread_id"))
            generic_thread_id = (
                session_link.external_session_id if session_link is not None else None
            )
            if (
                legacy_thread_id is not None
                and generic_thread_id is not None
                and legacy_thread_id != generic_thread_id
            ):
                raise StructuredSessionError(
                    "Codex compatibility and structured links disagree"
                )
            thread_id = generic_thread_id or legacy_thread_id
            forked_from = _optional_text(
                (self.legacy_link or {}).get("forked_from_thread_id")
            )
            if thread_id is None:
                response = self.runtime.client.request(
                    "thread/start",
                    {
                        **_thread_identity_params(self.request),
                        "ephemeral": False,
                    },
                    timeout=APP_SERVER_TIMEOUT_SECONDS,
                )
                thread_id = _thread_id(response)
            elif (
                str((self.legacy_link or {}).get("runtime_id") or "")
                != self.runtime.client.runtime_id
                or thread_id not in self.runtime.loaded_threads
            ):
                previous_status = str(
                    (self.legacy_link or {}).get("last_prompt_status") or ""
                )
                if previous_status == "submitted":
                    recovery_outcome = self._resolve_inflight_owner_loss(thread_id)
                else:
                    self._resume_thread(thread_id)
                    recovery_outcome = "resumed_after_owner_change"
        self.thread_id = thread_id
        self.runtime.loaded_threads.add(thread_id)
        now = utc_now()
        previous = self.legacy_link or {}
        self.legacy_link = self.supervisor.link_store.save(
            self.request.session_id or "",
            {
                "schema_version": APP_SERVER_LINK_SCHEMA_VERSION,
                "protocol": APP_SERVER_PROTOCOL,
                "runtime_id": self.runtime.client.runtime_id,
                "thread_id": thread_id,
                "latest_turn_id": (
                    session_link.latest_external_turn_id
                    if session_link is not None
                    else previous.get("latest_turn_id")
                ),
                "forked_from_thread_id": forked_from,
                "snapshot": dict(self.legacy_snapshot),
                "snapshot_hash": self.legacy_snapshot["snapshot_hash"],
                "runtime_status": "loaded",
                "recovery_outcome": recovery_outcome,
                "created_at": previous.get("created_at") or now,
                "resumed_at": (now if recovery_outcome.startswith("resumed") else None),
                "updated_at": now,
                "last_prompt_id": previous.get("last_prompt_id"),
                "last_prompt_status": previous.get("last_prompt_status"),
            },
        )
        return StructuredSessionState(
            thread_id,
            _optional_text(self.legacy_link.get("latest_turn_id")),
        )

    def start_turn(
        self,
        turn_input: StructuredTurnInput,
        event_sink: Callable[[Mapping[str, Any]], None],
        approval_bridge: Callable[[Mapping[str, Any]], str],
    ) -> StructuredTurnResult:
        """Run one Codex turn and publish normalized events through the driver sink."""
        if self.thread_id is None or self.legacy_link is None:
            raise StructuredSessionError("Codex structured session is not open")
        self._approval_bridge = approval_bridge
        response = self.runtime.client.request(
            "turn/start",
            {
                "threadId": self.thread_id,
                "input": [{"type": "text", "text": turn_input.content}],
                "clientUserMessageId": turn_input.id,
                "model": self.request.model,
                "cwd": self.request.workspace,
                "approvalPolicy": "on-request",
                "approvalsReviewer": "user",
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        turn_id = _turn_id(response)
        self.active_turn_id = turn_id
        self.legacy_link = self.supervisor.link_store.save(
            self.request.session_id or "",
            {
                **self.legacy_link,
                "latest_turn_id": turn_id,
                "runtime_status": "turn_running",
                "updated_at": utc_now(),
                "last_prompt_id": turn_input.id,
                "last_prompt_status": "submitted",
            },
        )

        def publish(event: HarnessEvent) -> None:
            event_sink(
                {
                    "type": event.type,
                    "message": event.message,
                    "payload": dict(event.payload),
                }
            )

        request = replace(self.request, event_sink=publish)
        self.result = self.supervisor._wait_for_turn(
            self.runtime,
            request,
            self.context,
            thread_id=self.thread_id,
            turn_id=turn_id,
            link=self.legacy_link,
            command_display=self.command_display,
            server_request_handler=self._handle_server_request,
        )
        status = "completed" if self.result.ok else "failed"
        raw_link = _mapping(self.result.raw.get("app_server_thread"))
        status = str(raw_link.get("last_prompt_status") or status)
        return StructuredTurnResult(turn_id, status)

    def respond_to_input(self, request_id: str, answer: str) -> None:
        """Reject unproven provider input requests in the app-server driver."""
        del request_id, answer
        raise UnsupportedSessionCapability("interactive input is not supported")

    def respond_to_approval(self, request_id: str, decision: str) -> None:
        """Persist one exact pending decision for the blocked durable bridge."""
        normalized = _normalize_provider_decision(decision)
        with self._pending_approval_lock:
            pending = self._pending_approvals.get(request_id)
        if pending is None or pending.durable_approval_id is None:
            raise StructuredSessionError("Codex approval request is stale or unknown")
        self.supervisor.runtime_store.decide_approval_request(
            pending.durable_approval_id,
            (
                ApprovalDecision.ALLOW_ONCE
                if normalized == "accept"
                else ApprovalDecision.DENY
            ),
        )

    def bind_durable_approval(self, request_id: str, approval_id: str) -> None:
        """Bind the provider callback to its persisted Approval Center item."""
        with self._pending_approval_lock:
            pending = self._pending_approvals.get(request_id)
            if pending is None:
                raise StructuredSessionError(
                    "Codex approval request is stale or unknown"
                )
            pending.durable_approval_id = approval_id

    def interrupt(self, turn_id: str) -> None:
        """Interrupt an exact active Codex turn."""
        if self.thread_id is None:
            raise StructuredSessionError("Codex structured session is not open")
        self.runtime.client.request(
            "turn/interrupt",
            {"threadId": self.thread_id, "turnId": turn_id},
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )

    def steer(self, turn_id: str, turn_input: StructuredTurnInput) -> None:
        """Steer the exact active turn through the reviewed v2 precondition."""
        if self.thread_id is None or self.active_turn_id != turn_id:
            raise StructuredSessionError("Codex turn is not active for steering")
        self.runtime.client.request(
            "turn/steer",
            {
                "threadId": self.thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": turn_input.content}],
                "clientUserMessageId": turn_input.id,
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )

    def recover(self, session_link: StructuredSessionLink) -> StructuredSessionState:
        """Recover the same provider thread without replaying normalized history."""
        self._resume_thread(session_link.external_session_id)
        self.thread_id = session_link.external_session_id
        self.runtime.loaded_threads.add(self.thread_id)
        return StructuredSessionState(
            self.thread_id,
            session_link.latest_external_turn_id,
        )

    def fork(
        self,
        session_link: StructuredSessionLink,
        turn_id: str | None,
    ) -> StructuredSessionState:
        """Create one provider-native fork without transcript replay."""
        response = self.runtime.client.request(
            "thread/fork",
            {
                **_thread_identity_params(self.request),
                "threadId": session_link.external_session_id,
                "lastTurnId": turn_id,
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        thread_id = _thread_id(response)
        self.thread_id = thread_id
        self.runtime.loaded_threads.add(thread_id)
        return StructuredSessionState(thread_id)

    def close(self) -> None:
        """Leave the shared supervised runtime available for other sessions."""

    def mark_interrupted(self, prompt_id: str) -> dict[str, Any] | None:
        """Persist a bounded compatibility outcome after one driver failure."""
        if self.legacy_link is None or self.thread_id is None:
            return self.legacy_link
        self.legacy_link = self.supervisor.link_store.save(
            self.request.session_id or "",
            {
                **self.legacy_link,
                "runtime_status": "interrupted",
                "recovery_outcome": (
                    self.legacy_link.get("recovery_outcome")
                    if self.legacy_link.get("recovery_outcome")
                    == "ambiguous_after_owner_loss"
                    else "owner_error"
                ),
                "updated_at": utc_now(),
                "last_prompt_status": (
                    "interrupted"
                    if self.legacy_link.get("last_prompt_id") == prompt_id
                    else self.legacy_link.get("last_prompt_status")
                ),
            },
        )
        return self.legacy_link

    def _resume_thread(self, thread_id: str) -> None:
        self._read_thread(thread_id, include_turns=False)
        self.runtime.client.request(
            "thread/resume",
            {
                **_thread_identity_params(self.request),
                "threadId": thread_id,
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )

    def _read_thread(
        self,
        thread_id: str,
        *,
        include_turns: bool,
    ) -> Mapping[str, Any]:
        response = self.runtime.client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": include_turns},
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        return _mapping(response.get("thread"))

    def _resolve_inflight_owner_loss(self, thread_id: str) -> str:
        """Recover only a provider-proven terminal turn after owner loss."""
        thread = self._read_thread(thread_id, include_turns=True)
        latest_turn_id = _optional_text((self.legacy_link or {}).get("latest_turn_id"))
        status = _matching_turn_status(thread, latest_turn_id)
        if status not in {
            "completed",
            "failed",
            "canceled",
            "cancelled",
            "interrupted",
        }:
            now = utc_now()
            self.legacy_link = self.supervisor.link_store.save(
                self.request.session_id or "",
                {
                    **(self.legacy_link or {}),
                    "runtime_id": self.runtime.client.runtime_id,
                    "runtime_status": "interrupted",
                    "recovery_outcome": "ambiguous_after_owner_loss",
                    "updated_at": now,
                    "last_prompt_status": "ambiguous",
                },
            )
            raise AppServerProtocolError(
                "Codex owner loss left the active turn outcome ambiguous; "
                "refusing duplicate delivery"
            )
        self.runtime.client.request(
            "thread/resume",
            {
                **_thread_identity_params(self.request),
                "threadId": thread_id,
            },
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        if self.legacy_link is not None:
            self.legacy_link = {
                **self.legacy_link,
                "last_prompt_status": status,
            }
        return f"resumed_after_terminal_{status}"

    def _handle_server_request(
        self,
        message: Mapping[str, Any],
        request: HarnessRequest,
        collected: list[HarnessEvent],
        timeout_seconds: float,
    ) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = _mapping(message.get("params"))
        if (
            method not in _APPROVAL_METHODS
            or not isinstance(request_id, (str, int))
            or self.thread_id is None
            or self.active_turn_id is None
            or params.get("threadId") != self.thread_id
            or params.get("turnId") != self.active_turn_id
        ):
            _decline_server_request(
                self.runtime.client,
                message,
                collected,
                request,
            )
            return
        key = str(request_id)
        pending = _PendingApproval(request_id, method, params)
        with self._pending_approval_lock:
            if key in self._pending_approvals:
                _decline_server_request(
                    self.runtime.client,
                    message,
                    collected,
                    request,
                )
                return
            self._pending_approvals[key] = pending
        try:
            if self._approval_bridge is None:
                raise UnsupportedSessionCapability(
                    "Codex approval bridge is unavailable"
                )
            decision = self._approval_bridge(
                {
                    "id": request_id,
                    "method": method,
                    "params": params,
                    "timeout_seconds": timeout_seconds,
                }
            )
            normalized = _normalize_provider_decision(decision)
        except Exception:
            normalized = "decline"
            _publish(
                request,
                collected,
                HarnessEvent(
                    type=HarnessEventType.WARNING.value,
                    message="Codex approval bridge failed closed.",
                    payload={"method": method, "enforcement": "fail_closed"},
                ),
            )
        with self._pending_approval_lock:
            current = self._pending_approvals.pop(key, None)
        if current is None:
            return
        self.runtime.client.respond(
            request_id,
            result=_approval_response(method, normalized, params),
        )
