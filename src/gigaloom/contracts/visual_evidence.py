"""Bounded local-origin visual gate receipt contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Protocol, cast, runtime_checkable

from gigaloom.contracts.operational_evidence import (
    OperationalEvidenceStatus,
    OperationalEvidenceV1,
)
from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    normalize_identities,
    validate_digest,
    validate_identity,
    validate_local_origin,
    validate_relative_path,
    validate_schema_version,
)


VISUAL_GATE_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION


class VisualGateStatus(str, Enum):
    """Reusable visual gate terminal status."""

    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class VisualViewportV1:
    """One exact browser viewport."""

    viewport_id: str
    width: int
    height: int
    device_scale_factor: float = 1.0
    schema_version: int = VISUAL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="visual viewport")
        validate_identity(self.viewport_id, field_name="visual viewport id")
        for value, label in (
            (self.width, "visual viewport width"),
            (self.height, "visual viewport height"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 16_384
            ):
                raise ValueError(f"{label} is invalid")
        if (
            isinstance(self.device_scale_factor, bool)
            or not isinstance(self.device_scale_factor, (int, float))
            or not math.isfinite(self.device_scale_factor)
            or not 0.5 <= self.device_scale_factor <= 4.0
        ):
            raise ValueError("visual device scale factor is invalid")


@dataclass(frozen=True, slots=True)
class VisualEvidenceSummaryV1:
    """Content-free bounded console or request summary."""

    total_count: int
    failure_count: int
    evidence_digest: str
    omissions: tuple[str, ...] = ()
    schema_version: int = VISUAL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="visual summary")
        for value, label in (
            (self.total_count, "visual summary total count"),
            (self.failure_count, "visual summary failure count"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} is invalid")
        if self.failure_count > self.total_count:
            raise ValueError("visual summary failures exceed total count")
        validate_digest(self.evidence_digest, field_name="visual summary digest")
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="visual summary omissions",
            ),
        )


@dataclass(frozen=True, slots=True)
class VisualArtifactReferenceV1:
    """Bounded redacted screenshot artifact reference."""

    artifact_id: str
    viewport_id: str
    relative_path: str
    artifact_digest: str
    media_type: str
    byte_count: int
    redacted: bool
    schema_version: int = VISUAL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="visual artifact")
        validate_identity(self.artifact_id, field_name="visual artifact id")
        validate_identity(self.viewport_id, field_name="visual viewport id")
        validate_relative_path(
            self.relative_path,
            field_name="visual artifact path",
        )
        validate_digest(self.artifact_digest, field_name="visual artifact digest")
        if self.media_type != "image/png":
            raise ValueError("visual screenshot media type must be image/png")
        if (
            isinstance(self.byte_count, bool)
            or not isinstance(self.byte_count, int)
            or not 1 <= self.byte_count <= 20 * 1024 * 1024
        ):
            raise ValueError("visual artifact byte count is invalid")
        if self.redacted is not True:
            raise ValueError("visual screenshot artifacts must be redacted")


@dataclass(frozen=True, slots=True)
class VisualTolerancePolicyV1:
    """Explicit non-flaky visual gate tolerance bounds."""

    policy_digest: str
    max_console_errors: int
    max_request_failures: int
    max_overflow_pixels: int
    max_timing_variance_ms: int
    max_attempts: int = 1
    required_passes: int = 1
    schema_version: int = VISUAL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="visual tolerance")
        validate_digest(self.policy_digest, field_name="visual tolerance digest")
        for value, upper, label in (
            (self.max_console_errors, 1_000, "max console errors"),
            (self.max_request_failures, 1_000, "max request failures"),
            (self.max_overflow_pixels, 100_000, "max overflow pixels"),
            (self.max_timing_variance_ms, 60_000, "max timing variance"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= upper
            ):
                raise ValueError(f"{label} is invalid")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 5
            or isinstance(self.required_passes, bool)
            or not isinstance(self.required_passes, int)
            or not 1 <= self.required_passes <= self.max_attempts
        ):
            raise ValueError("visual flake policy is invalid")


@dataclass(frozen=True, slots=True)
class VisualGateReceiptV1:
    """Reusable, redacted, local-origin visual gate evidence."""

    gate_id: str
    origin: str
    origin_policy_digest: str
    source_revision: str
    browser_fingerprint: str
    viewports: tuple[VisualViewportV1, ...]
    assertions: tuple[OperationalEvidenceV1, ...]
    console_summary: VisualEvidenceSummaryV1
    request_summary: VisualEvidenceSummaryV1
    screenshot_artifacts: tuple[VisualArtifactReferenceV1, ...]
    redaction_receipt: OperationalEvidenceV1
    tolerance_policy: VisualTolerancePolicyV1
    status: VisualGateStatus
    schema_version: int = VISUAL_GATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="visual gate receipt")
        validate_identity(self.gate_id, field_name="visual gate id")
        validate_local_origin(self.origin, field_name="visual gate origin")
        validate_digest(
            self.origin_policy_digest,
            field_name="visual origin policy digest",
        )
        validate_identity(self.source_revision, field_name="visual source revision")
        validate_digest(
            self.browser_fingerprint,
            field_name="visual browser fingerprint",
        )
        viewports = _normalize_viewports(self.viewports)
        object.__setattr__(self, "viewports", viewports)
        assertions = _normalize_assertions(self.assertions)
        object.__setattr__(self, "assertions", assertions)
        if not isinstance(self.console_summary, VisualEvidenceSummaryV1):
            raise ValueError("visual console summary is invalid")
        if not isinstance(self.request_summary, VisualEvidenceSummaryV1):
            raise ValueError("visual request summary is invalid")
        artifacts = _normalize_artifacts(
            self.screenshot_artifacts,
            viewport_ids={item.viewport_id for item in viewports},
        )
        object.__setattr__(self, "screenshot_artifacts", artifacts)
        if not isinstance(self.redaction_receipt, OperationalEvidenceV1):
            raise ValueError("visual redaction receipt is invalid")
        if not isinstance(self.tolerance_policy, VisualTolerancePolicyV1):
            raise ValueError("visual tolerance policy is invalid")
        if not isinstance(self.status, VisualGateStatus):
            raise ValueError("visual gate status is invalid")
        if self.status is VisualGateStatus.PASSED and (
            any(item.status is OperationalEvidenceStatus.FAILED for item in assertions)
            or self.redaction_receipt.status is not OperationalEvidenceStatus.PASSED
            or self.console_summary.failure_count
            > self.tolerance_policy.max_console_errors
            or self.request_summary.failure_count
            > self.tolerance_policy.max_request_failures
        ):
            raise ValueError("passed visual gate exceeds required evidence policy")


@runtime_checkable
class VisualGateReceiptPort(Protocol):
    """Public persistence boundary for reusable visual gate receipts."""

    def save(self, receipt: VisualGateReceiptV1) -> None:
        """Persist one validated visual gate receipt."""


def _normalize_viewports(values: object) -> tuple[VisualViewportV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) != 2
        or any(not isinstance(item, VisualViewportV1) for item in values)
    ):
        raise ValueError("visual gate requires exactly two viewports")
    typed = cast(tuple[VisualViewportV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.viewport_id))
    if normalized[0].viewport_id == normalized[1].viewport_id:
        raise ValueError("visual viewport ids must be distinct")
    if sum(item.width == 390 and item.height == 844 for item in normalized) != 1:
        raise ValueError("visual gate requires one 390x844 mobile viewport")
    return normalized


def _normalize_assertions(
    values: object,
) -> tuple[OperationalEvidenceV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 256
        or any(not isinstance(item, OperationalEvidenceV1) for item in values)
    ):
        raise ValueError("visual assertions must be a bounded evidence tuple")
    typed = cast(tuple[OperationalEvidenceV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.evidence_id))
    ids = [item.evidence_id for item in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError("visual assertion ids must be unique")
    return normalized


def _normalize_artifacts(
    values: object,
    *,
    viewport_ids: set[str],
) -> tuple[VisualArtifactReferenceV1, ...]:
    if (
        not isinstance(values, tuple)
        or not 2 <= len(values) <= 8
        or any(not isinstance(item, VisualArtifactReferenceV1) for item in values)
    ):
        raise ValueError("visual screenshots must be a bounded artifact tuple")
    typed = cast(tuple[VisualArtifactReferenceV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.artifact_id))
    artifact_ids = [item.artifact_id for item in normalized]
    if len(set(artifact_ids)) != len(artifact_ids):
        raise ValueError("visual screenshot artifact ids must be unique")
    bound_viewports = {item.viewport_id for item in normalized}
    if bound_viewports != viewport_ids:
        raise ValueError("visual screenshots must cover every admitted viewport")
    return normalized
