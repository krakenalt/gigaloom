"""Exact context-compaction lifecycle for an already owned app-server thread."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Any, Mapping

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    APP_SERVER_MESSAGE_POLL_SECONDS,
    APP_SERVER_TIMEOUT_SECONDS,
    AppServerClient,
    AppServerProtocolError,
    _Runtime,
)
from gigaloom.harnesses.builtins.codex.app_server.links import CodexAppServerLinkStore
from gigaloom.structured_sessions import StructuredSessionError


@dataclass(frozen=True, slots=True)
class AppServerCompactionOutcome:
    """Content-free evidence that upstream compaction completed."""

    thread_digest: str
    turn_id: str
    item_id: str


@dataclass(frozen=True, slots=True)
class AppServerCompactionOwner:
    """Live supervisor state required by the isolated compaction operation."""

    link_store: CodexAppServerLinkStore
    active_driver_lock: threading.Lock
    active_drivers: Mapping[str, Any]
    runtime_lock: threading.Lock
    runtimes: Mapping[str, _Runtime]


def compact_supervised_thread(
    owner: AppServerCompactionOwner,
    session_id: str,
) -> AppServerCompactionOutcome:
    """Resolve and lock the exact live app-server owner for one session."""
    with owner.active_driver_lock:
        if session_id in owner.active_drivers:
            raise StructuredSessionError("Codex thread is currently running")
    link = owner.link_store.load(session_id)
    if link is None:
        raise StructuredSessionError("Codex thread has not been started")
    runtime_id = str(link.get("runtime_id") or "")
    thread_id = str(link.get("thread_id") or "")
    with owner.runtime_lock:
        runtime = next(
            (
                item
                for item in owner.runtimes.values()
                if item.client.runtime_id == runtime_id and item.client.alive
            ),
            None,
        )
    if runtime is None:
        raise StructuredSessionError(
            "Codex app-server owner is unavailable; start a turn before compacting"
        )
    with runtime.turn_lock:
        return compact_loaded_thread(runtime.client, thread_id)


def compact_loaded_thread(
    client: AppServerClient,
    thread_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> AppServerCompactionOutcome:
    """Request compaction and require matching started/completed notifications."""
    if not thread_id or len(thread_id) > 256 or timeout_seconds <= 0:
        raise ValueError("Codex compaction scope is invalid")
    accepted = client.request(
        "thread/compact/start",
        {"threadId": thread_id},
        timeout=min(timeout_seconds, APP_SERVER_TIMEOUT_SECONDS),
    )
    if accepted != {}:
        raise AppServerProtocolError("Codex compaction acceptance payload changed")
    deadline = time.monotonic() + timeout_seconds
    started: tuple[str, str] | None = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        message = client.next_message(
            timeout=min(APP_SERVER_MESSAGE_POLL_SECONDS, max(remaining, 0.0))
        )
        if message is None:
            continue
        if "id" in message:
            request_id = message.get("id")
            if isinstance(request_id, (str, int)):
                client.respond(
                    request_id,
                    error={"code": -32601, "message": "unsupported during compaction"},
                )
            continue
        method = str(message.get("method") or "")
        params = _mapping(message.get("params"))
        if params.get("threadId") != thread_id:
            continue
        item = _mapping(params.get("item"))
        if item.get("type") != "contextCompaction":
            continue
        turn_id = _identity(params.get("turnId"))
        item_id = _identity(item.get("id"))
        if method == "item/started":
            started = (turn_id, item_id)
        elif method == "item/completed" and started == (turn_id, item_id):
            return AppServerCompactionOutcome(
                thread_digest=hashlib.sha256(thread_id.encode()).hexdigest(),
                turn_id=turn_id,
                item_id=item_id,
            )
    raise TimeoutError("Codex context compaction did not complete")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise AppServerProtocolError("Codex compaction lifecycle identity is invalid")
    return value


__all__ = [
    "AppServerCompactionOwner",
    "AppServerCompactionOutcome",
    "compact_loaded_thread",
    "compact_supervised_thread",
]
