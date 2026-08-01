"""Lazy public facade for automation backends shared across surfaces."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_VISUAL_GATE_EXPORTS = (
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
    "PlaywrightVisualBrowser",
    "ScreenshotRedactionSpec",
    "VisualArtifactStorePort",
    "VisualBrowserAdmission",
    "VisualBrowserPort",
    "VisualGateStorePort",
    "VisualProcessNetworkGrant",
    "VisualRedactionPolicy",
    "VisualEvalAssertion",
    "VisualEvalResult",
    "admit_visual_target",
    "collect_browser_evidence",
    "project_visual_gate_evidence",
    "run_visual_gate",
    "run_visual_eval",
    "validate_navigation_url",
)

__all__ = [*_VISUAL_GATE_EXPORTS]

_LAZY_EXPORTS = {
    name: ("gigaloom.automation.evaluations.visual.api", name)
    for name in _VISUAL_GATE_EXPORTS
}


def __getattr__(name: str) -> Any:
    """Load the selected automation backend only when requested."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the bounded automation surface to introspection."""
    return sorted({*globals(), *_LAZY_EXPORTS})
