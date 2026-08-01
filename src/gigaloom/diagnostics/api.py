"""Lazy public facade for operational diagnostics backends."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_FAULT_LAB_EXPORTS = (
    "ActiveDataRootRejectedError",
    "FaultFixtureId",
    "FaultInvariant",
    "FaultLabRunner",
    "FaultScenarioResult",
    "FaultScenarioStatus",
)
_RECOVERY_EXPORTS = (
    "CHECK_CATALOG",
    "MAX_PROJECTED_RECOVERY_CHECKS",
    "RECOVERY_PROJECTION_SCHEMA_VERSION",
    "RecoveryActionKind",
    "RecoveryActionPreview",
    "RecoveryActionStatus",
    "RecoveryCheckResult",
    "RecoveryCheckService",
    "RecoveryCheckStatus",
    "RecoveryPreviewReport",
    "RecoveryReceiptService",
    "RecoveryScanLimits",
    "RecoveryScanReport",
    "build_recovery_receipt",
    "fault_scenario_result_to_dict",
    "recovery_scan_report_to_dict",
)
_UPGRADE_RADAR_EXPORTS = (
    "CaseObservationV1",
    "CompatibilityDeltaV1",
    "ComparisonPolicyV1",
    "DeltaDirection",
    "GateObservationV1",
    "RecommendationDecisionV1",
    "RecommendationPolicyV1",
    "RouteEvaluationV1",
    "RouteSnapshotV1",
    "SealedCorpusCaseV1",
    "SealedCorpusV1",
    "UpgradeComparisonV1",
    "build_upgrade_report",
    "compare_route_evaluations",
    "load_sealed_corpus",
    "load_upgrade_report",
    "recommend_upgrade",
    "save_upgrade_report",
    "upgrade_report_bytes",
    "upgrade_report_from_bytes",
)

__all__ = [
    *_FAULT_LAB_EXPORTS,
    *_RECOVERY_EXPORTS,
    *_UPGRADE_RADAR_EXPORTS,
]

_LAZY_EXPORTS = {
    **{name: ("gigaloom.diagnostics.fault_lab", name) for name in _FAULT_LAB_EXPORTS},
    **{name: ("gigaloom.diagnostics.recovery", name) for name in _RECOVERY_EXPORTS},
    **{
        name: ("gigaloom.diagnostics.upgrade_radar", name)
        for name in _UPGRADE_RADAR_EXPORTS
    },
}


def __getattr__(name: str) -> Any:
    """Load the selected diagnostics backend only when requested."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the bounded diagnostics surface to introspection."""
    return sorted({*globals(), *_LAZY_EXPORTS})
