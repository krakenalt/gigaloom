"""Recommendation-only model and provider upgrade evidence."""

from gigaloom.diagnostics.upgrade_radar.contracts import (
    RouteSnapshotV1,
    SealedCorpusCaseV1,
    SealedCorpusV1,
)
from gigaloom.diagnostics.upgrade_radar.comparison import (
    CaseObservationV1,
    CompatibilityDeltaV1,
    ComparisonPolicyV1,
    DeltaDirection,
    GateObservationV1,
    RouteEvaluationV1,
    UpgradeComparisonV1,
    compare_route_evaluations,
)
from gigaloom.diagnostics.upgrade_radar.corpus import (
    load_named_sealed_corpus,
    load_sealed_corpus,
)
from gigaloom.diagnostics.upgrade_radar.operator import (
    UpgradeRadarCheckResult,
    UpgradeRadarReportStore,
    UpgradeRadarService,
    upgrade_radar_check_to_dict,
    upgrade_radar_report_list_to_dict,
)
from gigaloom.diagnostics.upgrade_radar.policy import (
    RecommendationDecisionV1,
    RecommendationPolicyV1,
    build_upgrade_report,
    recommend_upgrade,
)
from gigaloom.diagnostics.upgrade_radar.reports import (
    load_upgrade_report,
    save_upgrade_report,
    upgrade_report_bytes,
    upgrade_report_from_bytes,
)

__all__ = [
    "RouteSnapshotV1",
    "RouteEvaluationV1",
    "SealedCorpusCaseV1",
    "SealedCorpusV1",
    "CaseObservationV1",
    "CompatibilityDeltaV1",
    "ComparisonPolicyV1",
    "DeltaDirection",
    "GateObservationV1",
    "RecommendationDecisionV1",
    "RecommendationPolicyV1",
    "UpgradeComparisonV1",
    "build_upgrade_report",
    "compare_route_evaluations",
    "load_named_sealed_corpus",
    "load_sealed_corpus",
    "load_upgrade_report",
    "recommend_upgrade",
    "save_upgrade_report",
    "upgrade_report_bytes",
    "upgrade_report_from_bytes",
    "UpgradeRadarCheckResult",
    "UpgradeRadarReportStore",
    "UpgradeRadarService",
    "upgrade_radar_check_to_dict",
    "upgrade_radar_report_list_to_dict",
]
