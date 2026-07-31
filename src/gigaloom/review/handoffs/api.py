"""Public review facade for handoff capsules."""

from .models import (
    HANDOFF_CAPSULE_SCHEMA_VERSION,
    MAX_CAPSULE_ARTIFACTS,
    MAX_CAPSULE_QUESTIONS,
    HandoffCapsuleError,
    EnvironmentSnapshotProvider,
    HandoffCapsule,
)
from .service import HandoffCapsuleService
from .validation import verify_handoff_capsule

__all__ = [
    "HANDOFF_CAPSULE_SCHEMA_VERSION",
    "MAX_CAPSULE_ARTIFACTS",
    "MAX_CAPSULE_QUESTIONS",
    "HandoffCapsuleError",
    "EnvironmentSnapshotProvider",
    "HandoffCapsule",
    "HandoffCapsuleService",
    "verify_handoff_capsule",
]
