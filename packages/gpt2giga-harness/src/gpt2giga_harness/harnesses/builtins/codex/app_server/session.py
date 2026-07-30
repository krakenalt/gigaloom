"""Supervised Codex app-server process and session continuity."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping
from uuid import uuid4

from gpt2giga_harness.executables import ExecutableResolution
from gpt2giga_harness.harnesses.agent_cli import build_safe_env
from gpt2giga_harness.managed_mcp import (
    clear_headless_mcp_materialization,
    materialize_headless_mcp_snapshot,
)
from gpt2giga_harness.harnesses.ports import utc_now
from gpt2giga_harness.structured_sessions import (
    StructuredSessionConfigSnapshot,
    StructuredSessionCoordinator,
    StructuredSessionError,
    StructuredSessionLinkStore,
    StructuredTurnInput,
    structured_session_link_to_dict,
)
from gpt2giga_harness.types import (
    HarnessContext,
    HarnessEvent,
    HarnessRequest,
    HarnessResult,
    redact_secrets,
)

from gpt2giga_harness.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_DRIVER_PROTOCOL_VERSION,
    APP_SERVER_MESSAGE_POLL_SECONDS,
    APP_SERVER_PROTOCOL,
    APP_SERVER_ROLLOUT_POLL_SECONDS,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerClient,
    AppServerProtocolError,
    _Runtime,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.links import (
    CodexAppServerLinkStore,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.process import (
    _StdioJsonRpcClient,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.protocol import (
    _collab_thread_ids,
    _collab_tool,
    _normalize_notification,
    _turn_text,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.rollout import (
    _RolloutMultiAgentTail,
    _collab_child_tool_events,
    _public_collab_subagent,
    _read_collab_subagents,
    _rollout_multi_agent_events,
    _rollout_path_for_thread,
    _tool_event_identity,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.snapshots import (
    build_structured_execution_snapshot,
)
from gpt2giga_harness.harnesses.builtins.codex.app_server.utils import (
    _adapter_version,
    _cancel_requested,
    _driver_version,
    _mapping,
    _optional_text,
    _public_link,
    _publish,
    _safe_id,
    _scope_id,
    _structured_link_id,
    _validate_link_snapshot,
    _write_provider_config,
)

if TYPE_CHECKING:
    from gpt2giga_harness.harnesses.builtins.codex.app_server.driver import (
        CodexAppServerDriver,
    )


class CodexAppServerSupervisor:
    """Reuse compatible app-server processes while persisting thread ownership."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        client_factory: Callable[..., AppServerClient] | None = None,
    ) -> None:
        from gpt2giga_harness.harnesses.ports import RuntimeCoordinationStore

        self.data_dir = Path(data_dir).expanduser().resolve()
        self.link_store = CodexAppServerLinkStore(self.data_dir)
        self.structured_link_store = StructuredSessionLinkStore(self.data_dir)
        self.runtime_store = RuntimeCoordinationStore(self.data_dir)
        self.client_factory = client_factory or _StdioJsonRpcClient
        self._runtimes: dict[str, _Runtime] = {}
        self._runtime_lock = threading.Lock()
        self._active_drivers: dict[str, CodexAppServerDriver] = {}
        self._active_driver_lock = threading.Lock()

    def interrupt_turn(self, session_id: str) -> None:
        """Interrupt the exact active provider turn for one Harness session."""
        driver = self._active_driver(session_id)
        turn_id = driver.active_turn_id
        if turn_id is None:
            raise StructuredSessionError("Codex session has no active turn")
        driver.interrupt(turn_id)

    def steer_turn(self, session_id: str, turn_input: StructuredTurnInput) -> None:
        """Steer the exact active provider turn without transcript replay."""
        driver = self._active_driver(session_id)
        turn_id = driver.active_turn_id
        if turn_id is None:
            raise StructuredSessionError("Codex session has no active turn")
        driver.steer(turn_id, turn_input)

    def _active_driver(self, session_id: str) -> CodexAppServerDriver:
        with self._active_driver_lock:
            driver = self._active_drivers.get(session_id)
        if driver is None:
            raise StructuredSessionError("Codex session has no active driver")
        return driver

    def run_turn(
        self,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        resolution: ExecutableResolution,
        prompt: str,
        continuation: Mapping[str, Any],
    ) -> HarnessResult:
        """Start, continue, recover, or fork one structured Codex thread."""
        if request.session_id is None:
            raise ValueError(
                "Codex app-server continuity requires a Harness session id"
            )
        snapshot = _mapping(continuation.get("snapshot"))
        snapshot_hash = str(snapshot.get("snapshot_hash") or "")
        if len(snapshot_hash) != 64:
            raise ValueError("Codex app-server execution snapshot is invalid")
        prompt_id = str(continuation.get("prompt_id") or "").strip()
        if not prompt_id:
            raise ValueError("Codex app-server prompt id is required")
        command = resolution.command
        if not command:
            raise ValueError("Codex app-server executable is unavailable")
        scope_id = _scope_id(
            command,
            {
                **snapshot,
                "cli_version": _driver_version(
                    continuation.get("cli_version")
                    or continuation.get("adapter_version")
                ),
            },
        )
        runtime = self._runtime_for(
            scope_id,
            command=command,
            request=request,
            context=context,
            snapshot=snapshot,
        )
        command_display = (*command, "app-server", "--stdio", "--strict-config")
        with runtime.turn_lock:
            return self._run_through_driver(
                runtime,
                request,
                context,
                prompt=prompt,
                prompt_id=prompt_id,
                continuation=continuation,
                snapshot=snapshot,
                command_display=command_display,
            )

    def _run_through_driver(
        self,
        runtime: _Runtime,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        prompt: str,
        prompt_id: str,
        continuation: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        command_display: tuple[str, ...],
    ) -> HarnessResult:
        assert request.session_id is not None
        stored_link = self.link_store.load(request.session_id)
        supplied_link = _mapping(continuation.get("link"))
        link = stored_link or supplied_link or None
        if link is not None:
            _validate_link_snapshot(link, snapshot)
            if link.get("last_prompt_id") == prompt_id:
                return HarnessResult(
                    ok=False,
                    text="",
                    raw={"app_server_thread": _public_link(link)},
                    command=command_display,
                    error=(
                        "Codex app-server prompt was already submitted; refusing "
                        "duplicate delivery."
                    ),
                )

        adapter_version = _adapter_version()
        cli_version = _driver_version(
            continuation.get("cli_version") or continuation.get("adapter_version")
        )
        execution_snapshot = build_structured_execution_snapshot(
            snapshot,
            adapter_version=adapter_version,
            cli_version=cli_version,
        )
        config_snapshot = StructuredSessionConfigSnapshot(
            adapter_id="codex-cli",
            adapter_version=adapter_version,
            protocol=APP_SERVER_PROTOCOL,
            protocol_version=APP_SERVER_DRIVER_PROTOCOL_VERSION,
            cli_sdk_version=cli_version,
            managed_home_id=_optional_text(snapshot.get("managed_home_id")),
        )
        from gpt2giga_harness.harnesses.builtins.codex.app_server.driver import (
            CodexAppServerDriver,
        )

        driver = CodexAppServerDriver(
            supervisor=self,
            runtime=runtime,
            request=request,
            context=context,
            command_display=command_display,
            continuation=continuation,
            legacy_snapshot=snapshot,
            legacy_link=link,
            adapter_version=adapter_version,
        )
        coordinator = StructuredSessionCoordinator(
            driver,
            self.structured_link_store,
            owner_id=runtime.client.runtime_id,
        )
        link_id = _structured_link_id(request.session_id)
        existing = self.structured_link_store.load(link_id)
        collected: list[HarnessEvent] = []

        def event_sink(event: Mapping[str, Any]) -> None:
            normalized = HarnessEvent(
                type=str(event.get("type") or "unknown"),
                message=str(event.get("message") or ""),
                payload=_mapping(event.get("payload")),
            )
            _publish(request, collected, normalized)

        try:
            structured_link = coordinator.open_or_resume(
                link_id=link_id,
                harness_session_id=request.session_id,
                harness_run_id=request.run_id or f"run-{_safe_id(prompt_id)}",
                execution_snapshot=execution_snapshot,
                config_snapshot=config_snapshot,
                existing_link=existing,
            )
            with self._active_driver_lock:
                self._active_drivers[request.session_id] = driver
            try:
                structured_link, _turn = coordinator.start_turn(
                    structured_link,
                    StructuredTurnInput(prompt_id, prompt),
                    event_sink,
                    lambda provider_request: self._await_durable_approval(
                        request,
                        context,
                        collected,
                        provider_request,
                    ),
                )
            finally:
                with self._active_driver_lock:
                    if self._active_drivers.get(request.session_id) is driver:
                        self._active_drivers.pop(request.session_id, None)
            result = driver.result
            if result is None:
                raise AppServerProtocolError(
                    "Codex app-server driver returned no turn result"
                )
            return replace(
                result,
                raw={
                    **dict(result.raw),
                    "structured_session_link": structured_session_link_to_dict(
                        structured_link
                    ),
                    "structured_session_driver": "codex-app-server",
                },
                events=tuple(collected),
            )
        except Exception as exc:
            link = driver.mark_interrupted(prompt_id)
            return HarnessResult(
                ok=False,
                text="",
                raw={
                    "app_server_thread": _public_link(link),
                    **(
                        {
                            "structured_session_link": structured_session_link_to_dict(
                                current
                            )
                        }
                        if (current := self.structured_link_store.load(link_id))
                        is not None
                        else {}
                    ),
                },
                events=tuple(collected),
                command=command_display,
                error=str(redact_secrets(str(exc))),
            )

    def _await_durable_approval(
        self,
        request: HarnessRequest,
        context: HarnessContext,
        collected: list[HarnessEvent],
        provider_request: Mapping[str, Any],
    ) -> str:
        """Persist one provider request and await its Approval Center decision."""
        from gpt2giga_harness.harnesses.builtins.codex.app_server.approvals import (
            await_durable_approval,
        )

        return await_durable_approval(
            self,
            request,
            context,
            collected,
            provider_request,
        )

    def _wait_for_turn(
        self,
        runtime: _Runtime,
        request: HarnessRequest,
        context: HarnessContext,
        *,
        thread_id: str,
        turn_id: str,
        link: Mapping[str, Any],
        command_display: tuple[str, ...],
        server_request_handler: Callable[
            [Mapping[str, Any], HarnessRequest, list[HarnessEvent], float], None
        ],
    ) -> HarnessResult:
        collected: list[HarnessEvent] = []
        final_text = ""
        deadline = time.monotonic() + max(context.timeout_seconds, 0.1)
        interrupt_sent = False
        subagent_parents: dict[str, str] = {}
        seen_subagent_events: set[tuple[str, str, str]] = set()
        seen_rollout_events: set[tuple[str, str]] = set()
        next_rollout_poll = 0.0
        rollout_tail: _RolloutMultiAgentTail | None = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_rollout_poll:
                if rollout_tail is None:
                    rollout_path = _rollout_path_for_thread(
                        runtime.client,
                        home=(
                            self.data_dir / "app_server" / "homes" / runtime.scope_id
                        ),
                        thread_id=thread_id,
                    )
                    if rollout_path is not None:
                        rollout_tail = _RolloutMultiAgentTail(rollout_path, turn_id)
                if rollout_tail is not None:
                    for rollout_event in _rollout_multi_agent_events(
                        runtime.client,
                        tail=rollout_tail,
                        seen=seen_rollout_events,
                        child_seen=seen_subagent_events,
                    ):
                        _publish(request, collected, rollout_event)
                next_rollout_poll = now + APP_SERVER_ROLLOUT_POLL_SECONDS
            if _cancel_requested(request.cancel_event) and not interrupt_sent:
                runtime.client.request(
                    "turn/interrupt",
                    {"threadId": thread_id, "turnId": turn_id},
                    timeout=APP_SERVER_TIMEOUT_SECONDS,
                )
                interrupt_sent = True
            message = runtime.client.next_message(
                timeout=min(
                    APP_SERVER_MESSAGE_POLL_SECONDS,
                    max(deadline - time.monotonic(), 0.0),
                )
            )
            if message is None:
                if not runtime.client.alive:
                    raise AppServerProtocolError(
                        "Codex app-server owner exited before turn completion"
                    )
                continue
            method = str(message.get("method") or "")
            params = _mapping(message.get("params"))
            if "id" in message:
                server_request_handler(
                    message,
                    request,
                    collected,
                    max(deadline - time.monotonic(), 0.1),
                )
                continue
            if params.get("threadId") not in {None, thread_id}:
                continue
            if params.get("turnId") not in {None, turn_id}:
                continue
            subagent_snapshots: tuple[dict[str, Any], ...] = ()
            item = _mapping(params.get("item"))
            if (
                method in {"item/started", "item/completed"}
                and str(item.get("type") or "") == "collabToolCall"
            ):
                subagent_snapshots = _read_collab_subagents(runtime.client, item)
                if subagent_snapshots:
                    enriched_item = {
                        **item,
                        "subagents": [
                            _public_collab_subagent(snapshot)
                            for snapshot in subagent_snapshots
                        ],
                    }
                    params = {**params, "item": enriched_item}
                    item = enriched_item
                if _collab_tool(item) == "spawn_agent":
                    parent_id = str(item.get("id") or "")
                    for child_id in _collab_thread_ids(item):
                        if parent_id:
                            subagent_parents[child_id] = parent_id
            if method == "turn/completed" and rollout_tail is not None:
                for rollout_event in _rollout_multi_agent_events(
                    runtime.client,
                    tail=rollout_tail,
                    seen=seen_rollout_events,
                    child_seen=seen_subagent_events,
                ):
                    _publish(request, collected, rollout_event)
            event, item_text = _normalize_notification(method, params)
            if item_text is not None:
                final_text = item_text
            if event is not None:
                identity = _tool_event_identity(event)
                if identity is None or identity not in seen_rollout_events:
                    if identity is not None:
                        seen_rollout_events.add(identity)
                    _publish(request, collected, event)
            if method == "item/completed" and subagent_snapshots:
                for child_event in _collab_child_tool_events(
                    subagent_snapshots,
                    subagent_parents=subagent_parents,
                    seen=seen_subagent_events,
                ):
                    _publish(request, collected, child_event)
            if method == "turn/completed":
                turn = _mapping(params.get("turn"))
                status = str(turn.get("status") or "failed")
                final_text = _turn_text(turn) or final_text
                completed_link = self.link_store.save(
                    request.session_id or "",
                    {
                        **link,
                        "runtime_status": "loaded",
                        "updated_at": utc_now(),
                        "last_prompt_status": status,
                    },
                )
                ok = status == "completed"
                return HarnessResult(
                    ok=ok,
                    text=final_text if ok else "",
                    raw={
                        "app_server_thread": _public_link(completed_link),
                        "continuation_strategy": "structured_thread",
                    },
                    events=tuple(collected),
                    command=command_display,
                    error=None if ok else f"Codex app-server turn {status}.",
                )
        runtime.client.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout=APP_SERVER_TIMEOUT_SECONDS,
        )
        raise TimeoutError("Codex app-server turn timed out and was interrupted")

    def _runtime_for(
        self,
        scope_id: str,
        *,
        command: tuple[str, ...],
        request: HarnessRequest,
        context: HarnessContext,
        snapshot: Mapping[str, Any],
    ) -> _Runtime:
        with self._runtime_lock:
            current = self._runtimes.get(scope_id)
            if current is not None and current.client.alive:
                return current
            if current is not None:
                current.client.close()
            home = self.data_dir / "app_server" / "homes" / scope_id
            home.mkdir(parents=True, exist_ok=True)
            _write_provider_config(home, request, context)
            managed_mcp = _mapping(request.extra.get("managed_mcp_snapshot")) or None
            if managed_mcp is not None:
                materialize_headless_mcp_snapshot(
                    "codex-cli",
                    home,
                    managed_mcp,
                    data_dir=self.data_dir,
                )
            env = build_safe_env(
                context,
                extra={
                    "CODEX_HOME": str(home),
                    "GPT2GIGA_API_KEY": context.api_key or "0",
                    "GPT2GIGA_HARNESS_PROXY_URL": context.proxy_url,
                    "GPT2GIGA_HARNESS_API_MODE": request.api_mode.value,
                },
            )
            try:
                client = self.client_factory(
                    command=(*command, "app-server", "--stdio", "--strict-config"),
                    env=env,
                    cwd=str(self.data_dir),
                    runtime_id=f"asrv_{scope_id}_{uuid4().hex[:16]}",
                )
            finally:
                if managed_mcp is not None:
                    clear_headless_mcp_materialization("codex-cli", home)
            runtime = _Runtime(scope_id=scope_id, client=client)
            self._runtimes[scope_id] = runtime
            return runtime
