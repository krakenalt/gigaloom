"""Bounded timeout and process-signal cancellation for headless runs."""

from __future__ import annotations

from contextlib import AbstractContextManager
import signal
import threading
from types import FrameType
from typing import Any, Callable


_SignalHandler = Callable[[int, FrameType | None], Any] | int | None


class HeadlessCancellationToken:
    """Thread-safe cancellation view combining local and caller state."""

    def __init__(self, external: object | None = None) -> None:
        self._external = external
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: str | None = None

    def is_set(self) -> bool:
        """Return whether local or external cancellation was requested."""
        checker = getattr(self._external, "is_set", None)
        external_set = bool(checker()) if callable(checker) else False
        return self._event.is_set() or external_set

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for local cancellation, then include external state."""
        self._event.wait(timeout)
        return self.is_set()

    @property
    def reason(self) -> str | None:
        """Return the first bounded local reason, or an external marker."""
        with self._lock:
            local = self._reason
        if local is not None:
            return local
        checker = getattr(self._external, "is_set", None)
        if callable(checker) and bool(checker()):
            return "external_cancel"
        return None

    def cancel(self, reason: str) -> None:
        """Set cancellation exactly once."""
        if reason not in {"timeout", "signal_sigint", "signal_sigterm"}:
            raise ValueError("headless cancellation reason is invalid")
        with self._lock:
            if self._reason is None:
                self._reason = reason
                self._event.set()


class HeadlessCancellationScope(AbstractContextManager[HeadlessCancellationToken]):
    """Install temporary signal handlers and one bounded timeout timer."""

    def __init__(
        self,
        *,
        timeout_seconds: int,
        external: object | None = None,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self.token = HeadlessCancellationToken(external)
        self._timer = timer_factory(timeout_seconds, self.token.cancel, ("timeout",))
        self._previous: dict[signal.Signals, _SignalHandler] = {}

    def __enter__(self) -> HeadlessCancellationToken:
        self._install_signal_handlers()
        self._timer.daemon = True
        self._timer.start()
        return self.token

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._timer.cancel()
        for signum, previous in self._previous.items():
            signal.signal(signum, previous)
        self._previous.clear()
        return None

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self._handle_signal)

    def _handle_signal(
        self,
        signum: int,
        _frame: FrameType | None,
    ) -> None:
        reason = "signal_sigint" if signum == signal.SIGINT else "signal_sigterm"
        self.token.cancel(reason)


__all__ = ["HeadlessCancellationScope", "HeadlessCancellationToken"]
