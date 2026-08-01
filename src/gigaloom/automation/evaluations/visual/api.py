"""Stable public boundary for the Visual QA gate."""

from gigaloom.automation.evaluations.visual.admission import (
    admit_visual_target,
    validate_navigation_url,
)
from gigaloom.automation.evaluations.visual.artifacts import (
    FilesystemVisualArtifactStore,
    VisualArtifactStorePort,
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
from gigaloom.automation.evaluations.visual.redaction import (
    ScreenshotRedactionSpec,
    VisualRedactionPolicy,
)
from gigaloom.automation.evaluations.visual.reports import (
    FilesystemVisualGateStore,
    VisualGateStorePort,
    project_visual_gate_evidence,
    run_visual_gate,
)
from gigaloom.automation.evaluations.visual.operator import (
    VisualEvalAssertion,
    VisualEvalResult,
    run_visual_eval,
)
from gigaloom.automation.evaluations.visual.playwright_adapter import (
    PlaywrightVisualBrowser,
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
    "FilesystemVisualArtifactStore",
    "FilesystemVisualGateStore",
    "ScreenshotRedactionSpec",
    "VisualBrowserAdmission",
    "VisualBrowserPort",
    "VisualArtifactStorePort",
    "VisualGateStorePort",
    "VisualProcessNetworkGrant",
    "VisualRedactionPolicy",
    "VisualEvalAssertion",
    "VisualEvalResult",
    "PlaywrightVisualBrowser",
    "admit_visual_target",
    "collect_browser_evidence",
    "project_visual_gate_evidence",
    "run_visual_gate",
    "run_visual_eval",
    "validate_navigation_url",
]
