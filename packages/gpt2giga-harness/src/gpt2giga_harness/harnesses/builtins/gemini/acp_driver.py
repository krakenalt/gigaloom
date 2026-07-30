"""Gemini ACP process and structured-session lifecycle."""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from gpt2giga_harness.execution import ExecutionSnapshot
from gpt2giga_harness.structured_processes import (
    NormalizedStructuredEvent,
    StructuredBridgeKind,
    StructuredBridgeRequest,
    StructuredProcessState,
    StructuredProcessSupervisor,
    StructuredRequestHandle,
    StructuredTransport,
)
from gpt2giga_harness.structured_sessions import (
    AdapterCapabilitySnapshot,
    StructuredSessionLink,
    StructuredSessionState,
    StructuredTurnInput,
    StructuredTurnResult,
    UnsupportedSessionCapability,
)


from gpt2giga_harness.harnesses.builtins.gemini.acp_contracts import (
    AuthProvider,
    Clock,
    GEMINI_ACP_PERMISSION_METHOD,
    GEMINI_ACP_PROTOCOL_VERSION,
    GeminiAcpError,
    GeminiAcpHandshake,
    McpProvider,
    _validate_identity,
    probe_gemini_acp_handshake,
)
from gpt2giga_harness.harnesses.builtins.gemini.acp_protocol import (
    _approval_contract,
    _canonical_uuid,
    _degradation_evidence,
    _json_mapping,
    _permission_option,
    _require_text,
    _turn_status,
    normalize_gemini_acp_event,
)


class GeminiAcpDriver:
    """Operate one Gemini ACP session through an isolated supervised process."""

    def __init__(
        self,
        transport_factory: Callable[[], StructuredTransport],
        *,
        cli_help: str,
        cli_version: str,
        adapter_version: str,
        cwd: str,
        auth_provider: AuthProvider,
        mcp_provider: McpProvider | None = None,
        request_timeout_seconds: float = 5.0,
        prompt_timeout_seconds: float = 300.0,
        idle_ttl_seconds: float = 300.0,
        bridge_timeout_seconds: float = 30.0,
        clock: Clock = time.monotonic,
    ) -> None:
        for name, value in (
            ("request_timeout_seconds", request_timeout_seconds),
            ("prompt_timeout_seconds", prompt_timeout_seconds),
            ("idle_ttl_seconds", idle_ttl_seconds),
            ("bridge_timeout_seconds", bridge_timeout_seconds),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be numeric")
            if value <= 0 or not math.isfinite(value):
                raise ValueError(f"{name} must be finite and positive")
        self.cli_help = cli_help
        self.cli_version = cli_version
        self.adapter_version = adapter_version
        self.cwd = _require_text(cwd, field_name="cwd")
        self.auth_provider = auth_provider
        self.mcp_provider = mcp_provider or (lambda: ())
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.prompt_timeout_seconds = float(prompt_timeout_seconds)
        self.idle_ttl_seconds = float(idle_ttl_seconds)
        self.clock = clock
        self.supervisor = StructuredProcessSupervisor(
            transport_factory,
            event_normalizer=normalize_gemini_acp_event,
            approval_methods=frozenset({GEMINI_ACP_PERMISSION_METHOD}),
            bridge_timeout_seconds=bridge_timeout_seconds,
        )
        self.handshake: GeminiAcpHandshake | None = None
        self.session_id: str | None = None
        self.active_turn_id: str | None = None
        self._last_activity = self.clock()
        self._pending_permissions: dict[str, StructuredBridgeRequest] = {}
        self._permission_lock = threading.Lock()

    def probe(self) -> AdapterCapabilitySnapshot:
        """Return capabilities proven by the current installed ACP handshake."""
        return self._ensure_ready().capability_snapshot

    def open_or_resume(
        self,
        execution_snapshot: ExecutionSnapshot,
        session_link: StructuredSessionLink | None,
    ) -> StructuredSessionState:
        """Open a new UUID session or load the exact linked UUID without replay."""
        del execution_snapshot
        handshake = self._ensure_ready()
        mcp_servers = self._mcp_servers(handshake)
        if session_link is None:
            result = self.supervisor.request(
                "session/new",
                {"cwd": self.cwd, "mcpServers": mcp_servers},
                timeout=self.request_timeout_seconds,
            )
            session_id = _canonical_uuid(
                result.get("sessionId"), field_name="ACP session id"
            )
        else:
            if not handshake.capability_snapshot.resume:
                raise UnsupportedSessionCapability(
                    "Gemini ACP loadSession is unsupported"
                )
            session_id = _canonical_uuid(
                session_link.external_session_id,
                field_name="ACP linked session id",
            )
            self.supervisor.request(
                "session/load",
                {
                    "sessionId": session_id,
                    "cwd": self.cwd,
                    "mcpServers": mcp_servers,
                },
                timeout=self.request_timeout_seconds,
            )
        self.session_id = session_id
        self._touch()
        return StructuredSessionState(
            session_id,
            session_link.latest_external_turn_id if session_link is not None else None,
            _degradation_evidence(self.cli_version),
        )

    def start_turn(
        self,
        turn_input: StructuredTurnInput,
        event_sink: Callable[[Mapping[str, Any]], None],
        approval_bridge: Callable[[Mapping[str, Any]], str],
    ) -> StructuredTurnResult:
        """Run one ACP prompt while servicing permission requests and events."""
        session_id = self._require_session()
        if self.active_turn_id is not None:
            raise GeminiAcpError("Gemini ACP already has an active turn")
        handle = self.supervisor.begin_request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": turn_input.content}],
            },
        )
        turn_id = handle.id
        self.active_turn_id = turn_id
        deadline = self.clock() + self.prompt_timeout_seconds
        try:
            while not handle.done:
                remaining = deadline - self.clock()
                if remaining <= 0:
                    self.supervisor.notify("session/cancel", {"sessionId": session_id})
                    return self._finish_timed_out(handle, turn_id)
                wait = min(remaining, 0.05)
                permission = self.supervisor.next_bridge_request(timeout=wait / 2)
                if permission is not None:
                    self._bridge_permission(permission, approval_bridge)
                event = self.supervisor.next_event(timeout=wait / 2)
                if event is not None:
                    self._publish_event(event, event_sink)
            result = handle.result(0.001)
            self._drain_events(event_sink)
            stop_reason = result.get("stopReason")
            status = _turn_status(stop_reason)
            self._touch()
            return StructuredTurnResult(turn_id, status)
        finally:
            self.active_turn_id = None

    def respond_to_input(self, request_id: str, answer: str) -> None:
        """Reject general elicitation because only permission requests are proven."""
        del request_id, answer
        raise UnsupportedSessionCapability("Gemini ACP elicitation is unsupported")

    def respond_to_approval(self, request_id: str, decision: str) -> None:
        """Respond to one exact live permission request from another caller."""
        with self._permission_lock:
            request = self._pending_permissions.pop(request_id, None)
        if request is None:
            raise GeminiAcpError("Gemini ACP permission request is stale or unknown")
        self._respond_permission(request, decision)

    def interrupt(self, turn_id: str) -> None:
        """Cancel only the exact active ACP session prompt."""
        if turn_id != self.active_turn_id:
            raise GeminiAcpError("Gemini ACP turn is not active")
        self.supervisor.notify("session/cancel", {"sessionId": self._require_session()})
        self._touch()

    def recover(self, session_link: StructuredSessionLink) -> StructuredSessionState:
        """Start a fresh generation and load the exact UUID without prompt replay."""
        session_id = _canonical_uuid(
            session_link.external_session_id,
            field_name="ACP linked session id",
        )
        handshake = self._restart_ready()
        if not handshake.capability_snapshot.recovery_after_process_loss:
            raise UnsupportedSessionCapability("Gemini ACP recovery is unsupported")
        self.supervisor.request(
            "session/load",
            {
                "sessionId": session_id,
                "cwd": self.cwd,
                "mcpServers": self._mcp_servers(handshake),
            },
            timeout=self.request_timeout_seconds,
        )
        self.session_id = session_id
        self._touch()
        return StructuredSessionState(
            session_id,
            session_link.latest_external_turn_id,
            _degradation_evidence(self.cli_version),
        )

    def recycle_if_idle(self) -> bool:
        """Recycle an inactive process after its bounded idle TTL."""
        if self.active_turn_id is not None:
            return False
        if self.supervisor.state is not StructuredProcessState.RUNNING:
            return False
        if self.clock() - self._last_activity < self.idle_ttl_seconds:
            return False
        self.supervisor.close()
        self.handshake = None
        self.session_id = None
        return True

    def close(self) -> None:
        """Stop process resources without claiming provider session-close support."""
        self.supervisor.close()
        self.handshake = None
        self.session_id = None
        self.active_turn_id = None

    def _ensure_ready(self) -> GeminiAcpHandshake:
        if self.supervisor.state is StructuredProcessState.NEW:
            self.supervisor.start()
        elif self.supervisor.state in {
            StructuredProcessState.LOST,
            StructuredProcessState.STOPPED,
        }:
            self.supervisor.restart()
        if self.handshake is None:
            self.handshake = self._initialize_and_authenticate()
        return self.handshake

    def _restart_ready(self) -> GeminiAcpHandshake:
        state = self.supervisor.state
        if state is StructuredProcessState.RUNNING:
            self.supervisor.close()
        self.handshake = None
        self.session_id = None
        return self._ensure_ready()

    def _initialize_and_authenticate(self) -> GeminiAcpHandshake:
        result = self.supervisor.request(
            "initialize",
            {
                "protocolVersion": GEMINI_ACP_PROTOCOL_VERSION,
                "clientInfo": {
                    "name": "gpt2giga-harness",
                    "version": self.adapter_version,
                },
                "clientCapabilities": {
                    "auth": {"terminal": False},
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
            },
            timeout=self.request_timeout_seconds,
        )
        handshake = probe_gemini_acp_handshake(
            cli_help=self.cli_help,
            cli_version=self.cli_version,
            adapter_version=self.adapter_version,
            result=result,
        )
        method_id, metadata = self.auth_provider()
        if method_id not in handshake.auth_method_ids:
            raise GeminiAcpError("ACP auth method was not advertised")
        params: dict[str, Any] = {"methodId": method_id}
        if metadata is not None:
            if not isinstance(metadata, Mapping):
                raise ValueError("ACP auth metadata must be a mapping")
            params["_meta"] = dict(metadata)
        self.supervisor.request(
            "authenticate", params, timeout=self.request_timeout_seconds
        )
        self._touch()
        return handshake

    def _mcp_servers(self, handshake: GeminiAcpHandshake) -> list[dict[str, Any]]:
        raw = self.mcp_provider()
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ValueError("Gemini ACP MCP provider must return a sequence")
        servers: list[dict[str, Any]] = []
        names: set[str] = set()
        for item in raw:
            server = _json_mapping(item, field_name="Gemini ACP MCP server")
            name = server.get("name")
            _validate_identity(name, field_name="Gemini ACP MCP server name")
            if name in names:
                raise GeminiAcpError("Gemini ACP MCP server names are duplicated")
            names.add(name)
            transport = server.get("type")
            if transport not in handshake.mcp_transports:
                raise UnsupportedSessionCapability(
                    "Gemini ACP MCP transport was not advertised"
                )
            servers.append(server)
        return servers

    def _bridge_permission(
        self,
        request: StructuredBridgeRequest,
        approval_bridge: Callable[[Mapping[str, Any]], str],
    ) -> None:
        if request.kind is not StructuredBridgeKind.APPROVAL:
            raise GeminiAcpError("unexpected Gemini ACP bridge kind")
        key = str(request.id)
        contract = _approval_contract(
            request,
            session_id=self._require_session(),
            turn_id=self.active_turn_id,
        )
        with self._permission_lock:
            self._pending_permissions[key] = request
        try:
            decision = approval_bridge(contract)
            with self._permission_lock:
                pending = self._pending_permissions.pop(key, None)
            if pending is not None:
                self._respond_permission(pending, decision)
        except Exception:
            with self._permission_lock:
                pending = self._pending_permissions.pop(key, None)
            if pending is not None:
                self._respond_permission(pending, "deny")
            raise

    def _respond_permission(
        self,
        request: StructuredBridgeRequest,
        decision: str,
    ) -> None:
        option_id = _permission_option(request.params, decision)
        outcome: Mapping[str, Any]
        if option_id is None:
            outcome = {"outcome": "cancelled"}
        else:
            outcome = {"outcome": "selected", "optionId": option_id}
        self.supervisor.respond_bridge(
            request.id,
            generation=request.generation,
            result={"outcome": outcome},
        )
        self._touch()

    def _publish_event(
        self,
        event: NormalizedStructuredEvent,
        event_sink: Callable[[Mapping[str, Any]], None],
    ) -> None:
        event_session_id = event.payload.get("session_id")
        if event_session_id is not None and event_session_id != self._require_session():
            raise GeminiAcpError("Gemini ACP event session UUID does not match")
        event_sink(
            {
                "type": event.type,
                "payload": dict(event.payload),
                "generation": event.generation,
                "synthetic": event.synthetic,
            }
        )

    def _drain_events(self, event_sink: Callable[[Mapping[str, Any]], None]) -> None:
        while (event := self.supervisor.next_event(timeout=0.0)) is not None:
            self._publish_event(event, event_sink)

    def _finish_timed_out(
        self,
        handle: StructuredRequestHandle,
        turn_id: str,
    ) -> StructuredTurnResult:
        try:
            result = handle.result(self.request_timeout_seconds)
        except Exception as exc:
            raise GeminiAcpError("Gemini ACP prompt timed out") from exc
        self._touch()
        return StructuredTurnResult(turn_id, _turn_status(result.get("stopReason")))

    def _require_session(self) -> str:
        if self.session_id is None:
            raise GeminiAcpError("Gemini ACP session is not open")
        return self.session_id

    def _touch(self) -> None:
        self._last_activity = self.clock()
