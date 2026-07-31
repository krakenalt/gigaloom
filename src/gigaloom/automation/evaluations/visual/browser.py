"""Browser adapter boundary for bounded Visual QA collection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from gigaloom.automation.evaluations.visual.assertions import DomAssertionSpec
from gigaloom.automation.evaluations.visual.contracts import (
    BrowserFingerprint,
    VisualBrowserAdmission,
)
from gigaloom.contracts import VisualViewportV1
from gigaloom.contracts.operational_validation import (
    validate_digest,
    validate_identity,
    validate_text,
)


MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024
MAX_CONSOLE_OBSERVATIONS = 256
MAX_DOM_OBSERVATIONS = 128


class BrowserConsoleLevel(str, Enum):
    """Bounded console levels retained without message content."""

    DEBUG = "debug"
    INFO = "info"
    LOG = "log"
    WARNING = "warning"
    ERROR = "error"
    ASSERT = "assert"


@dataclass(frozen=True, slots=True)
class BrowserConsoleObservation:
    """Content-free console observation."""

    level: BrowserConsoleLevel
    message_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.level, BrowserConsoleLevel):
            raise ValueError("visual console level is invalid")
        validate_digest(
            self.message_digest,
            field_name="visual console message digest",
        )


@dataclass(frozen=True, slots=True)
class BrowserRequestObservation:
    """Content-free browser request result and network-policy decision."""

    origin: str
    url_digest: str
    method: str
    status_code: int | None = None
    failure_code: str | None = None
    blocked: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "origin", _validate_request_origin(self.origin))
        validate_digest(self.url_digest, field_name="visual request URL digest")
        validate_identity(self.method, field_name="visual request method")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or not 100 <= self.status_code <= 599
        ):
            raise ValueError("visual request status code is invalid")
        if self.failure_code is not None:
            validate_identity(
                self.failure_code,
                field_name="visual request failure code",
            )
        if not isinstance(self.blocked, bool):
            raise ValueError("visual request blocked state is invalid")

    @property
    def failed(self) -> bool:
        """Return whether this request contributes to failure evidence."""
        return bool(
            self.blocked
            or self.failure_code is not None
            or (self.status_code is not None and self.status_code >= 400)
        )


@dataclass(frozen=True, slots=True)
class BrowserDomObservation:
    """Content-free DOM measurement keyed to one requested assertion."""

    assertion_id: str
    matched_count: int
    visible_count: int
    text_digest: str | None = None

    def __post_init__(self) -> None:
        validate_identity(self.assertion_id, field_name="visual assertion id")
        for value, field_name in (
            (self.matched_count, "visual DOM matched count"),
            (self.visible_count, "visual DOM visible count"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 10_000
            ):
                raise ValueError(f"{field_name} is invalid")
        if self.visible_count > self.matched_count:
            raise ValueError("visual DOM visible count exceeds matched count")
        if self.text_digest is not None:
            validate_digest(
                self.text_digest,
                field_name="visual DOM text digest",
            )


@dataclass(frozen=True, slots=True)
class BrowserCaptureRequest:
    """One isolated viewport capture requested under an admitted grant."""

    admission: VisualBrowserAdmission
    viewport: VisualViewportV1
    assertions: tuple[DomAssertionSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.viewport not in self.admission.viewports:
            raise ValueError("visual capture viewport was not admitted")
        if (
            not isinstance(self.assertions, tuple)
            or len(self.assertions) > MAX_DOM_OBSERVATIONS
            or any(not isinstance(item, DomAssertionSpec) for item in self.assertions)
        ):
            raise ValueError("visual DOM assertions must be a bounded tuple")
        ids = [item.assertion_id for item in self.assertions]
        if len(ids) != len(set(ids)):
            raise ValueError("visual DOM assertion ids must be unique")


@dataclass(frozen=True, slots=True)
class BrowserCaptureResult:
    """One bounded raw viewport capture returned by a browser adapter."""

    viewport_id: str
    final_url: str
    redirects: tuple[str, ...]
    browser_fingerprint: str
    screenshot_png: bytes
    console: tuple[BrowserConsoleObservation, ...]
    requests: tuple[BrowserRequestObservation, ...]
    dom: tuple[BrowserDomObservation, ...]
    client_width: int
    scroll_width: int
    timing_ms: int

    def __post_init__(self) -> None:
        validate_identity(self.viewport_id, field_name="visual capture viewport id")
        validate_text(self.final_url, field_name="visual final URL", max_chars=2_048)
        _validate_redirects(self.redirects)
        validate_digest(
            self.browser_fingerprint,
            field_name="visual capture browser fingerprint",
        )
        if (
            not isinstance(self.screenshot_png, bytes)
            or not 8 <= len(self.screenshot_png) <= MAX_SCREENSHOT_BYTES
            or not self.screenshot_png.startswith(b"\x89PNG\r\n\x1a\n")
        ):
            raise ValueError("visual screenshot must be a bounded PNG")
        _validate_observations(
            self.console,
            BrowserConsoleObservation,
            maximum=MAX_CONSOLE_OBSERVATIONS,
            field_name="visual console observations",
        )
        _validate_observations(
            self.requests,
            BrowserRequestObservation,
            maximum=512,
            field_name="visual request observations",
        )
        _validate_observations(
            self.dom,
            BrowserDomObservation,
            maximum=MAX_DOM_OBSERVATIONS,
            field_name="visual DOM observations",
        )
        dom_ids = [item.assertion_id for item in self.dom]
        if len(dom_ids) != len(set(dom_ids)):
            raise ValueError("visual DOM observation ids must be unique")
        for value, field_name in (
            (self.client_width, "visual client width"),
            (self.scroll_width, "visual scroll width"),
            (self.timing_ms, "visual timing"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 1_000_000
            ):
                raise ValueError(f"{field_name} is invalid")

    @property
    def overflow_pixels(self) -> int:
        """Return horizontal overflow without allowing negative evidence."""
        return max(self.scroll_width - self.client_width, 0)


@runtime_checkable
class VisualBrowserPort(Protocol):
    """Isolated browser authority supplied by a later composition owner."""

    @property
    def identity(self) -> BrowserFingerprint:
        """Return the exact browser implementation identity."""

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        """Capture one admitted viewport in a fresh bounded browser context."""


def _validate_request_origin(value: object) -> str:
    text = validate_text(value, field_name="visual request origin", max_chars=2_048)
    parsed = urlsplit(text)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("visual request origin is invalid")
    return text.rstrip("/")


def _validate_redirects(values: object) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) > 8
        or any(not isinstance(item, str) or len(item) > 2_048 for item in values)
    ):
        raise ValueError("visual redirects must be a bounded URL tuple")


def _validate_observations(
    values: object,
    expected_type: type[object],
    *,
    maximum: int,
    field_name: str,
) -> None:
    if (
        not isinstance(values, tuple)
        or len(values) > maximum
        or any(not isinstance(item, expected_type) for item in values)
    ):
        raise ValueError(f"{field_name} must be a bounded tuple")


__all__ = [
    "BrowserCaptureRequest",
    "BrowserCaptureResult",
    "BrowserConsoleLevel",
    "BrowserConsoleObservation",
    "BrowserDomObservation",
    "BrowserRequestObservation",
    "VisualBrowserPort",
]
