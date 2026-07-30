"""Content-free duration hooks shared across execution boundaries."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar, Token


DiagnosticRecorder = Callable[[str, float], None]

_RECORDER: ContextVar[DiagnosticRecorder | None] = ContextVar(
    "harness_diagnostic_recorder",
    default=None,
)


def bind_diagnostic_recorder(recorder: DiagnosticRecorder) -> Token:
    """Bind a request-scoped, content-free diagnostic recorder."""
    return _RECORDER.set(recorder)


def reset_diagnostic_recorder(token: Token) -> None:
    """Restore the recorder that preceded one request boundary."""
    _RECORDER.reset(token)


def record_duration(metric: str, milliseconds: float) -> None:
    """Record a duration when the current request installed a recorder."""
    recorder = _RECORDER.get()
    if recorder is not None:
        recorder(metric, max(0.0, milliseconds))


for _public_primitive in (
    bind_diagnostic_recorder,
    record_duration,
    reset_diagnostic_recorder,
):
    _public_primitive.__module__ = "gpt2giga_harness.instrumentation"

__all__ = [
    "DiagnosticRecorder",
    "bind_diagnostic_recorder",
    "record_duration",
    "reset_diagnostic_recorder",
]
