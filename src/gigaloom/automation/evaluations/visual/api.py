"""Stable public boundary for the Visual QA gate."""

from gigaloom.automation.evaluations.visual.admission import (
    admit_visual_target,
    validate_navigation_url,
)
from gigaloom.automation.evaluations.visual.browser import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    VisualBrowserPort,
)
from gigaloom.automation.evaluations.visual.contracts import (
    BrowserFingerprint,
    VisualBrowserAdmission,
    VisualProcessNetworkGrant,
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
