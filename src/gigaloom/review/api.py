"""Lazy public facade for route decisions and Run Capsule review contracts."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_ROUTE_DECISION_EXPORTS = (
    "RouteDecisionBindingsV1",
    "RouteDecisionError",
    "RouteDecisionCostEvidenceV1",
    "RouteDecisionNotFoundError",
    "RouteDecisionReceiptV1",
    "RouteDecisionRepository",
    "RouteDecisionOverrideError",
    "RouteDecisionVerificationError",
    "RouteRunBindingError",
    "ConfirmedRouteRunPlanV1",
    "CurrentRouteRunEvidenceSource",
    "CurrentRouteRunEvidenceV1",
    "ExactStructuredRouteRunner",
    "RouteRunConfirmationV1",
    "create_route_decision_receipt",
    "execute_confirmed_route",
    "override_route_decision_receipt",
    "route_decision_receipt_from_dict",
    "route_decision_receipt_to_dict",
    "revalidate_route_decision_for_run",
    "verify_route_decision_receipt",
)
_CAPSULE_EXPORTS = (
    "ArtifactManifest",
    "CapsuleError",
    "CapsuleVerificationReport",
    "FilesystemRunCapsuleRepository",
    "InputLock",
    "OmissionManifest",
    "OutputReceipt",
    "RunCapsuleBundle",
    "RunCapsuleCapturePortsV1",
    "RunCapsuleEvidencePort",
    "RunCapsuleInputPort",
    "RunCapsuleLifecycleService",
    "RunCapsuleOutputPort",
    "RunCapsuleRecordV1",
    "build_artifact_manifest",
    "build_input_lock",
    "build_omission_manifest",
    "build_output_receipt",
    "capture_run_capsule",
    "capture_run_capsule_from_ports",
    "export_run_capsule",
    "verify_run_capsule",
)
_LANE_DELTA_EXPORTS = (
    "FilesystemLaneDeltaPacketStore",
    "LANE_DELTA_RECORD_KIND",
    "LANE_IDENTITY_FIELD_COUNT",
    "MAX_EXPLICIT_CONTENT_PACKET_BYTES",
    "MAX_LANE_ATTACHMENTS",
    "MAX_LANE_DELTA_PACKET_BYTES",
    "LaneAttachmentObservationV1",
    "LaneDeltaBuildError",
    "LaneDeltaBuildRequestV1",
    "LaneDeltaBuilder",
    "LaneDeltaConflictError",
    "LaneDeltaIntegrityError",
    "LaneDeltaStorageError",
    "StaleLaneSourceError",
    "StoredLaneDeltaPacketV1",
)

__all__ = [*_ROUTE_DECISION_EXPORTS, *_CAPSULE_EXPORTS, *_LANE_DELTA_EXPORTS]

_LAZY_EXPORTS = {
    **{
        name: ("gigaloom.review.route_decisions", name)
        for name in _ROUTE_DECISION_EXPORTS
    },
    **{name: ("gigaloom.review.capsules.api", name) for name in _CAPSULE_EXPORTS},
    **{
        name: ("gigaloom.review.handoffs.lane_delta.api", name)
        for name in _LANE_DELTA_EXPORTS
    },
}


def __getattr__(name: str) -> Any:
    """Load only the selected review contract boundary."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose the bounded public review surface to introspection."""
    return sorted({*globals(), *_LAZY_EXPORTS})
