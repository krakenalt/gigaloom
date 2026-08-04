"""Content-free local product evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TypeAlias, cast

from gigaloom.contracts.operational_validation import (
    OPERATIONAL_SCHEMA_VERSION,
    normalize_identities,
    validate_identity,
    validate_schema_version,
    validate_time_range,
    validate_timestamp,
)


PRODUCT_EVIDENCE_SCHEMA_VERSION = OPERATIONAL_SCHEMA_VERSION
MAX_PRODUCT_EVIDENCE_METRICS = 128
MAX_PRODUCT_EVIDENCE_SOURCES = 16
MAX_PRODUCT_EVIDENCE_RANGE = timedelta(days=366)

ProductMetricValue: TypeAlias = bool | int | str | None


class ProductMetricStatus(str, Enum):
    """Whether a metric is supported by retained owner facts."""

    OBSERVED = "observed"
    UNKNOWN = "unknown"


class ProductMetricUnit(str, Enum):
    """Bounded units admitted to the local report."""

    BOOLEAN = "boolean"
    COUNT = "count"
    DURATION_MS = "duration_ms"
    RATIO_BASIS_POINTS = "ratio_basis_points"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class ProductMetricV1:
    """One aggregate metric without event or conversation content."""

    metric_id: str
    status: ProductMetricStatus
    unit: ProductMetricUnit
    value: ProductMetricValue
    reason_code: str
    sample_size: int
    schema_version: int = PRODUCT_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="product metric")
        validate_identity(self.metric_id, field_name="product metric id")
        if not isinstance(self.status, ProductMetricStatus):
            raise ValueError("product metric status is invalid")
        if not isinstance(self.unit, ProductMetricUnit):
            raise ValueError("product metric unit is invalid")
        validate_identity(self.reason_code, field_name="product metric reason")
        if isinstance(self.sample_size, bool) or not isinstance(self.sample_size, int):
            raise ValueError("product metric sample size must be an integer")
        if self.sample_size < 0 or self.sample_size > 1_000_000:
            raise ValueError("product metric sample size is out of bounds")
        _validate_metric_value(self)


@dataclass(frozen=True, slots=True)
class ProductEvidenceSourceV1:
    """Bounded count of content-free facts read from one existing owner."""

    source_id: str
    observed_count: int
    truncated: bool
    schema_version: int = PRODUCT_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="product source")
        validate_identity(self.source_id, field_name="product source id")
        if (
            isinstance(self.observed_count, bool)
            or not isinstance(self.observed_count, int)
            or self.observed_count < 0
            or self.observed_count > 1_000_000
        ):
            raise ValueError("product source observed count is out of bounds")
        if not isinstance(self.truncated, bool):
            raise ValueError("product source truncated flag must be boolean")


@dataclass(frozen=True, slots=True)
class ProductEvidenceReportV1:
    """Deterministic, local-only beta evidence for one project and time range."""

    report_id: str
    project_id: str
    range_start: datetime
    range_end: datetime
    generated_at: datetime
    metrics: tuple[ProductMetricV1, ...]
    sources: tuple[ProductEvidenceSourceV1, ...]
    omissions: tuple[str, ...]
    content_free: bool = True
    local_only: bool = True
    schema_version: int = PRODUCT_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_schema_version(self.schema_version, field_name="product report")
        validate_identity(self.report_id, field_name="product report id")
        validate_identity(self.project_id, field_name="product report project id")
        validate_time_range(
            self.range_start,
            self.range_end,
            field_name="product report range",
        )
        validate_timestamp(self.generated_at, field_name="product report generated_at")
        if self.range_end - self.range_start > MAX_PRODUCT_EVIDENCE_RANGE:
            raise ValueError("product report range exceeds the bounded maximum")
        if self.generated_at < self.range_end:
            raise ValueError("product report cannot be generated before its range ends")
        metrics = _normalize_metrics(self.metrics)
        sources = _normalize_sources(self.sources)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "sources", sources)
        object.__setattr__(
            self,
            "omissions",
            normalize_identities(
                self.omissions,
                field_name="product report omissions",
            ),
        )
        if self.content_free is not True:
            raise ValueError("product report must be content-free")
        if self.local_only is not True:
            raise ValueError("product report must be local-only")


def _validate_metric_value(metric: ProductMetricV1) -> None:
    value = metric.value
    if metric.status is ProductMetricStatus.UNKNOWN:
        if value is not None or metric.sample_size != 0:
            raise ValueError("unknown product metric cannot carry a value or samples")
        return
    if value is None or metric.sample_size < 1:
        raise ValueError("observed product metric requires a value and samples")
    if metric.unit is ProductMetricUnit.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError("boolean product metric requires a boolean value")
        return
    if metric.unit is ProductMetricUnit.STATUS:
        validate_identity(value, field_name="product metric status value")
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("numeric product metric requires a non-negative integer")
    if metric.unit is ProductMetricUnit.RATIO_BASIS_POINTS and value > 10_000:
        raise ValueError("product metric ratio exceeds 100 percent")


def _normalize_metrics(values: object) -> tuple[ProductMetricV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_PRODUCT_EVIDENCE_METRICS
        or any(not isinstance(item, ProductMetricV1) for item in values)
    ):
        raise ValueError("product report metrics must be a bounded tuple")
    typed = cast(tuple[ProductMetricV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.metric_id))
    if len({item.metric_id for item in normalized}) != len(normalized):
        raise ValueError("product report metric ids must be unique")
    return normalized


def _normalize_sources(values: object) -> tuple[ProductEvidenceSourceV1, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > MAX_PRODUCT_EVIDENCE_SOURCES
        or any(not isinstance(item, ProductEvidenceSourceV1) for item in values)
    ):
        raise ValueError("product report sources must be a bounded tuple")
    typed = cast(tuple[ProductEvidenceSourceV1, ...], values)
    normalized = tuple(sorted(typed, key=lambda item: item.source_id))
    if len({item.source_id for item in normalized}) != len(normalized):
        raise ValueError("product report source ids must be unique")
    return normalized


__all__ = [
    "MAX_PRODUCT_EVIDENCE_METRICS",
    "MAX_PRODUCT_EVIDENCE_RANGE",
    "MAX_PRODUCT_EVIDENCE_SOURCES",
    "PRODUCT_EVIDENCE_SCHEMA_VERSION",
    "ProductEvidenceReportV1",
    "ProductEvidenceSourceV1",
    "ProductMetricStatus",
    "ProductMetricUnit",
    "ProductMetricV1",
]
