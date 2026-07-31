"""Stable public boundary for the Visual QA gate."""

from gigaloom.automation.evaluations.visual.admission import (
    admit_visual_target,
    validate_navigation_url,
)
from gigaloom.automation.evaluations.visual.assertions import (
    DomAssertionKind,
    DomAssertionSpec,
)
from gigaloom.automation.evaluations.visual.browser import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    BrowserConsoleLevel,
    BrowserConsoleObservation,
    BrowserDomObservation,
    BrowserRequestObservation,
    VisualBrowserPort,
)
from gigaloom.automation.evaluations.visual.contracts import (
    BrowserFingerprint,
    VisualBrowserAdmission,
    VisualProcessNetworkGrant,
)
from gigaloom.automation.evaluations.visual.evidence import (
    CollectedVisualEvidence,
    collect_browser_evidence,
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
