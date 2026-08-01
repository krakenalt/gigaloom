"""Reusable Visual QA Eval application service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from gigaloom.automation.evaluations.visual.artifacts import (
    FilesystemVisualArtifactStore,
    VisualArtifactStorePort,
)
from gigaloom.automation.evaluations.visual.browser import (
    BrowserCaptureRequest,
    BrowserCaptureResult,
    VisualBrowserPort,
)
from gigaloom.automation.evaluations.visual.contracts import (
    VisualProcessNetworkGrant,
)
from gigaloom.automation.evaluations.visual.admission import admit_visual_target
from gigaloom.automation.evaluations.visual.playwright_adapter import (
    PlaywrightVisualBrowser,
)
from gigaloom.automation.evaluations.visual.reports import (
    FilesystemVisualGateStore,
    VisualGateStorePort,
    project_visual_gate_evidence,
    run_visual_gate,
)
from gigaloom.automation.evaluations.visual.runtime import (
    LocalVisualTargetInspector,
)
from gigaloom.contracts import (
    OperationalEvidenceV1,
    VisualGateReceiptV1,
    VisualTolerancePolicyV1,
)
from gigaloom.contracts.operational_validation import canonical_digest


class VisualEvalAssertion(str, Enum):
    """Public built-in Visual QA Eval assertions."""

    NO_CONSOLE_ERRORS = "no-console-errors"
    NO_HORIZONTAL_OVERFLOW = "no-horizontal-overflow"


@dataclass(frozen=True, slots=True)
class VisualEvalResult:
    """One reusable gate receipt plus its Eval evidence projection."""

    requested_assertions: tuple[VisualEvalAssertion, ...]
    receipt: VisualGateReceiptV1
    eval_evidence: OperationalEvidenceV1


BrowserFactory = Callable[[], VisualBrowserPort]


class VisualTargetInspector(Protocol):
    """Read-only exact-listener inspection used by the Eval composition."""

    def resolve(self, target_url: str) -> VisualProcessNetworkGrant:
        """Resolve the exact admitted process/network grant."""

    def revalidate(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        """Revalidate the complete target identity before persistence."""

    def revalidate_listener(
        self,
        target_url: str,
        expected: VisualProcessNetworkGrant,
    ) -> None:
        """Revalidate the listener identity around a capture."""


def run_visual_eval(
    *,
    target_url: str,
    assertions: tuple[str, ...],
    evidence_root: Path,
    browser_factory: BrowserFactory | None = None,
    inspector: VisualTargetInspector | None = None,
    artifact_store: VisualArtifactStorePort | None = None,
    receipt_store: VisualGateStorePort | None = None,
) -> VisualEvalResult:
    """Run one deterministic-policy local Visual QA Eval gate."""
    requested = _normalize_assertions(assertions)
    target_inspector = inspector or LocalVisualTargetInspector()
    grant = target_inspector.resolve(target_url)
    browser = (
        browser_factory()
        if browser_factory is not None
        else PlaywrightVisualBrowser.discover()
    )
    guarded_browser = _ListenerGuardedBrowser(
        browser=browser,
        inspector=target_inspector,
        target_url=target_url,
        grant=grant,
    )
    admission = admit_visual_target(
        target_url=target_url,
        grant=grant,
        browser=browser.identity,
    )
    tolerance = _tolerance_policy(requested)
    root = evidence_root.resolve()
    receipt = run_visual_gate(
        browser=guarded_browser,
        admission=admission,
        artifact_store=artifact_store or FilesystemVisualArtifactStore(root),
        receipt_store=receipt_store or FilesystemVisualGateStore(root),
        tolerance_policy=tolerance,
        revalidate_admission=lambda: target_inspector.revalidate(target_url, grant),
    )
    return VisualEvalResult(
        requested_assertions=requested,
        receipt=receipt,
        eval_evidence=project_visual_gate_evidence(receipt, consumer="eval"),
    )


def _normalize_assertions(
    values: tuple[str, ...],
) -> tuple[VisualEvalAssertion, ...]:
    if not values:
        raise ValueError("Visual QA requires at least one --assert gate")
    try:
        normalized = tuple(VisualEvalAssertion(item) for item in values)
    except ValueError as error:
        raise ValueError("Visual QA assertion is unsupported") from error
    if len(set(normalized)) != len(normalized):
        raise ValueError("Visual QA assertions must be unique")
    return tuple(sorted(normalized, key=lambda item: item.value))


def _tolerance_policy(
    assertions: tuple[VisualEvalAssertion, ...],
) -> VisualTolerancePolicyV1:
    selected = frozenset(assertions)
    payload = {
        "assertions": [item.value for item in assertions],
        "max_attempts": 1,
        "max_request_failures": 0,
        "required_passes": 1,
        "version": 1,
    }
    return VisualTolerancePolicyV1(
        policy_digest=canonical_digest(payload),
        max_console_errors=(
            0 if VisualEvalAssertion.NO_CONSOLE_ERRORS in selected else 1_000
        ),
        max_request_failures=0,
        max_overflow_pixels=(
            0 if VisualEvalAssertion.NO_HORIZONTAL_OVERFLOW in selected else 100_000
        ),
        max_timing_variance_ms=0,
    )


class _ListenerGuardedBrowser:
    def __init__(
        self,
        *,
        browser: VisualBrowserPort,
        inspector: VisualTargetInspector,
        target_url: str,
        grant: VisualProcessNetworkGrant,
    ) -> None:
        self._browser = browser
        self._inspector = inspector
        self._target_url = target_url
        self._grant = grant
        self.identity = browser.identity

    def capture(self, request: BrowserCaptureRequest) -> BrowserCaptureResult:
        self._inspector.revalidate_listener(self._target_url, self._grant)
        result = self._browser.capture(request)
        self._inspector.revalidate_listener(self._target_url, self._grant)
        return result


__all__ = [
    "VisualEvalAssertion",
    "VisualEvalResult",
    "run_visual_eval",
]
