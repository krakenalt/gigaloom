"""Browser adapter boundary for bounded Visual QA collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gigaloom.automation.evaluations.visual.contracts import (
    BrowserFingerprint,
    VisualBrowserAdmission,
)
from gigaloom.contracts import VisualViewportV1


@dataclass(frozen=True, slots=True)
class BrowserCaptureRequest:
    """One isolated viewport capture requested under an admitted grant."""

    admission: VisualBrowserAdmission
    viewport: VisualViewportV1

    def __post_init__(self) -> None:
        if self.viewport not in self.admission.viewports:
            raise ValueError("visual capture viewport was not admitted")


@dataclass(frozen=True, slots=True)
class BrowserCaptureResult:
    """Minimal navigation result; evidence fields are added by collection."""

    final_url: str
    redirects: tuple[str, ...]
    browser_fingerprint: str


@runtime_checkable
class VisualBrowserPort(Protocol):
    """Isolated browser authority supplied by a later composition owner."""

    @property
    def identity(self) -> BrowserFingerprint:
        """Return the exact browser implementation identity."""

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        """Capture one admitted viewport in a fresh bounded browser context."""


__all__ = [
    "BrowserCaptureRequest",
    "BrowserCaptureResult",
    "VisualBrowserPort",
]
