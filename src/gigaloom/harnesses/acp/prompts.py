"""Bound ACP prompts with ACP-native cancellation and non-authoritative late results."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import TYPE_CHECKING

from acp.schema import PromptRequest, PromptResponse, TextContentBlock

from gigaloom.harnesses.acp.cancellation import cancel_session
from gigaloom.harnesses.acp.errors import (
    AcpProtocolError,
    AcpRequestCancelled,
    AcpRequestTimeout,
)
from gigaloom.harnesses.acp.sessions import AcpSessionBindingV1, require_session
from gigaloom.harnesses.acp.usage import AcpTokenUsageV1, project_token_usage
from gigaloom.structured_processes import StructuredRequestHandle

if TYPE_CHECKING:
    from gigaloom.harnesses.acp.client import AcpClient


@dataclass(frozen=True, slots=True)
class AcpPromptResultV1:
    """Content-free completion reason and optional exact token counters."""

    stop_reason: str
    usage: AcpTokenUsageV1 | None


class AcpPromptHandle:
    """Local prompt waiter whose cancellation wins over every late result."""

    def __init__(
        self,
        client: AcpClient,
        binding: AcpSessionBindingV1,
        request: StructuredRequestHandle,
    ) -> None:
        self._client = client
        self._binding = binding
        self._request = request
        self._cancelled = threading.Event()

    @property
    def generation(self) -> int:
        """Return the connection generation that owns the prompt."""
        return self._request.generation

    @property
    def done(self) -> bool:
        """Return whether cancellation or the wire request completed."""
        return self._cancelled.is_set() or self._request.done

    def cancel(self) -> bool:
        """Free the local waiter and send ACP cancellation exactly once."""
        if self.done:
            return False
        self._cancelled.set()
        cancel_session(self._client, self._binding)
        return True

    def result(self, timeout: float) -> AcpPromptResultV1:
        """Await a bounded result while preserving local cancellation authority."""
        if timeout <= 0:
            raise ValueError("ACP prompt timeout must be positive")
        deadline = time.monotonic() + timeout
        while not self._request.done:
            if self._cancelled.wait(min(0.01, max(0.0, deadline - time.monotonic()))):
                raise AcpRequestCancelled("ACP prompt waiter was cancelled")
            if time.monotonic() >= deadline:
                self._cancelled.set()
                cancel_session(self._client, self._binding)
                raise AcpRequestTimeout("ACP prompt waiter timed out")
        if self._cancelled.is_set():
            raise AcpRequestCancelled("ACP prompt waiter was cancelled")
        raw = self._request.result(max(0.001, deadline - time.monotonic()))
        try:
            response = PromptResponse.model_validate(raw)
        except Exception as exc:
            raise AcpProtocolError(
                "ACP prompt response failed schema validation"
            ) from exc
        return AcpPromptResultV1(
            response.stop_reason, project_token_usage(response.usage)
        )


def begin_prompt(
    client: AcpClient, binding: AcpSessionBindingV1, *, text: str
) -> AcpPromptHandle:
    """Begin a transient text prompt for an exact live session binding."""
    require_session(client, binding)
    if not text or "\x00" in text:
        raise ValueError("ACP prompt text is invalid")
    request = PromptRequest(
        session_id=binding.acp_session_id,
        prompt=[TextContentBlock(text=text, type="text")],
    )
    handle = client.supervisor.begin_request(
        "session/prompt",
        request.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    return AcpPromptHandle(client, binding, handle)
