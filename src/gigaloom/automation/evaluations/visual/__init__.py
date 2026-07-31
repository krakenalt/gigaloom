"""Public Visual QA gate API."""

from gigaloom.automation.evaluations.visual.api import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    BrowserConsoleObservation,
    BrowserDomObservation,
    BrowserFingerprint,
    BrowserRequestObservation,
    CollectedVisualEvidence,
    DomAssertionKind,
    DomAssertionSpec,
    VisualBrowserAdmission,
    VisualBrowserPort,
    VisualProcessNetworkGrant,
    admit_visual_target,
    collect_browser_evidence,
    validate_navigation_url,
)

__all__ = [
    "BrowserCaptureRequest",
    "BrowserCaptureResult",
    "BrowserConsoleLevel",
    "BrowserConsoleObservation",
    "BrowserDomObservation",
    "BrowserFingerprint",
    "BrowserRequestObservation",
    "CollectedVisualEvidence",
    "DomAssertionKind",
    "DomAssertionSpec",
    "VisualBrowserAdmission",
    "VisualBrowserPort",
    "VisualProcessNetworkGrant",
    "admit_visual_target",
    "collect_browser_evidence",
    "validate_navigation_url",
]
