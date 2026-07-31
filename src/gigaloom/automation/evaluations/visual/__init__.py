"""Public Visual QA gate API."""

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserFingerprint,
    VisualBrowserAdmission,
    VisualBrowserPort,
    VisualProcessNetworkGrant,
    admit_visual_target,
    validate_navigation_url,
)

__all__ = [
    "BrowserCaptureRequest",
    "BrowserCaptureResult",
    "BrowserFingerprint",
    "VisualBrowserAdmission",
    "VisualBrowserPort",
    "VisualProcessNetworkGrant",
    "admit_visual_target",
    "validate_navigation_url",
]
