"""Bounded Gemini provider authentication over ACP."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from gigaloom.structured_processes import (
    StdioJsonRpcTransport,
    StructuredProcessError,
    StructuredProcessSupervisor,
    StructuredRequestHandle,
)

from .broker import (
    AUTH_OUTPUT_BYTES,
    AUTH_STATUS_TIMEOUT_SECONDS,
    AuthenticationCommandResult,
    _validated_argument,
)


class GeminiAcpAuthenticationRunner:
    """Run the reviewed Gemini OAuth method through sequential bounded ACP calls."""

    def __init__(
        self,
        supervisor_factory: Callable[
            [tuple[str, ...], Mapping[str, str], Path], StructuredProcessSupervisor
        ]
        | None = None,
    ) -> None:
        self.supervisor_factory = supervisor_factory or _gemini_acp_supervisor

    def run(
        self,
        argv: Sequence[str],
        *,
        environment: Mapping[str, str],
        cwd: Path,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> AuthenticationCommandResult:
        command = tuple(_validated_argument(value) for value in argv)
        deadline = time.monotonic() + max(float(timeout_seconds), 0.05)
        supervisor = self.supervisor_factory(command, environment, cwd)
        try:
            supervisor.start()
            initialize = supervisor.begin_request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {
                        "name": "gigaloom",
                        "title": "GigaLoom",
                        "version": "1",
                    },
                },
            )
            initialized = _await_request(
                initialize,
                deadline=min(deadline, time.monotonic() + AUTH_STATUS_TIMEOUT_SECONDS),
                cancel_event=cancel_event,
            )
            if not _oauth_personal_advertised(initialized):
                return AuthenticationCommandResult(returncode=1)
            authenticate = supervisor.begin_request(
                "authenticate",
                {"methodId": "oauth-personal"},
            )
            _await_request(
                authenticate,
                deadline=deadline,
                cancel_event=cancel_event,
            )
            return AuthenticationCommandResult(returncode=0)
        except _Cancelled:
            return AuthenticationCommandResult(returncode=1, cancelled=True)
        except _TimedOut:
            return AuthenticationCommandResult(returncode=1, timed_out=True)
        except (StructuredProcessError, ValueError):
            return AuthenticationCommandResult(returncode=1)
        finally:
            supervisor.close()


class _Cancelled(RuntimeError):
    pass


class _TimedOut(RuntimeError):
    pass


def _await_request(
    handle: StructuredRequestHandle,
    *,
    deadline: float,
    cancel_event: threading.Event,
) -> Mapping[str, Any]:
    while not handle.done:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            handle.cancel()
            raise _TimedOut
        if cancel_event.wait(min(remaining, 0.05)):
            handle.cancel()
            raise _Cancelled
    return handle.result(0.05)


def _oauth_personal_advertised(initialize: Mapping[str, Any]) -> bool:
    if initialize.get("protocolVersion") != 1:
        return False
    methods = initialize.get("authMethods")
    return isinstance(methods, list) and any(
        isinstance(item, Mapping) and item.get("id") == "oauth-personal"
        for item in methods
    )


def _gemini_acp_supervisor(
    command: tuple[str, ...],
    environment: Mapping[str, str],
    cwd: Path,
) -> StructuredProcessSupervisor:
    runtime_id = f"provider-auth-gemini-{uuid4().hex}"
    return StructuredProcessSupervisor(
        lambda: StdioJsonRpcTransport(
            command=command,
            runtime_id=runtime_id,
            env=environment,
            cwd=os.fspath(cwd),
            read_queue_size=16,
            max_stderr_bytes=0,
        ),
        event_normalizer=lambda _method, _params: None,
        event_queue_size=4,
        bridge_queue_size=1,
        max_frame_bytes=AUTH_OUTPUT_BYTES,
        stop_timeout_seconds=2.0,
        max_pending_requests=1,
    )


__all__ = ["GeminiAcpAuthenticationRunner"]
