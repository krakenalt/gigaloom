"""Strict deterministic codec for local product evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from gigaloom.contracts.operational_validation import parse_timestamp, require_mapping
from gigaloom.contracts.product_evidence import (
    ProductEvidenceReportV1,
    ProductEvidenceSourceV1,
    ProductMetricStatus,
    ProductMetricUnit,
    ProductMetricV1,
)


def product_evidence_report_to_dict(
    report: ProductEvidenceReportV1,
) -> dict[str, Any]:
    """Serialize one content-free local report."""
    return {
        "schema_version": report.schema_version,
        "report_id": report.report_id,
        "project_id": report.project_id,
        "range_start": report.range_start.isoformat(),
        "range_end": report.range_end.isoformat(),
        "generated_at": report.generated_at.isoformat(),
        "metrics": [_metric_to_dict(item) for item in report.metrics],
        "sources": [_source_to_dict(item) for item in report.sources],
        "omissions": list(report.omissions),
        "content_free": report.content_free,
        "local_only": report.local_only,
    }


def product_evidence_report_from_dict(
    payload: Mapping[str, Any],
) -> ProductEvidenceReportV1:
    """Decode one strict local product evidence report."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "report_id",
            "project_id",
            "range_start",
            "range_end",
            "generated_at",
            "metrics",
            "sources",
            "omissions",
            "content_free",
            "local_only",
        },
        field_name="product evidence report",
    )
    return ProductEvidenceReportV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        report_id=_string(value["report_id"], "report_id"),
        project_id=_string(value["project_id"], "project_id"),
        range_start=parse_timestamp(value["range_start"], field_name="range_start"),
        range_end=parse_timestamp(value["range_end"], field_name="range_end"),
        generated_at=parse_timestamp(value["generated_at"], field_name="generated_at"),
        metrics=tuple(_metric_from_dict(item) for item in _objects(value["metrics"])),
        sources=tuple(_source_from_dict(item) for item in _objects(value["sources"])),
        omissions=_strings(value["omissions"], "omissions"),
        content_free=_boolean(value["content_free"], "content_free"),
        local_only=_boolean(value["local_only"], "local_only"),
    )


def product_evidence_report_bytes(report: ProductEvidenceReportV1) -> bytes:
    """Return canonical UTF-8 JSON with one trailing newline."""
    return (
        json.dumps(
            product_evidence_report_to_dict(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def product_evidence_report_digest(report: ProductEvidenceReportV1) -> str:
    """Digest one deterministic local report."""
    return hashlib.sha256(product_evidence_report_bytes(report)).hexdigest()


def _metric_to_dict(metric: ProductMetricV1) -> dict[str, Any]:
    return {
        "schema_version": metric.schema_version,
        "metric_id": metric.metric_id,
        "status": metric.status.value,
        "unit": metric.unit.value,
        "value": metric.value,
        "reason_code": metric.reason_code,
        "sample_size": metric.sample_size,
    }


def _metric_from_dict(payload: Mapping[str, Any]) -> ProductMetricV1:
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "metric_id",
            "status",
            "unit",
            "value",
            "reason_code",
            "sample_size",
        },
        field_name="product metric",
    )
    return ProductMetricV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        metric_id=_string(value["metric_id"], "metric_id"),
        status=ProductMetricStatus(_string(value["status"], "status")),
        unit=ProductMetricUnit(_string(value["unit"], "unit")),
        value=_metric_value(value["value"]),
        reason_code=_string(value["reason_code"], "reason_code"),
        sample_size=_integer(value["sample_size"], "sample_size"),
    )


def _source_to_dict(source: ProductEvidenceSourceV1) -> dict[str, Any]:
    return {
        "schema_version": source.schema_version,
        "source_id": source.source_id,
        "observed_count": source.observed_count,
        "truncated": source.truncated,
    }


def _source_from_dict(payload: Mapping[str, Any]) -> ProductEvidenceSourceV1:
    value = require_mapping(
        payload,
        required={"schema_version", "source_id", "observed_count", "truncated"},
        field_name="product evidence source",
    )
    return ProductEvidenceSourceV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        source_id=_string(value["source_id"], "source_id"),
        observed_count=_integer(value["observed_count"], "observed_count"),
        truncated=_boolean(value["truncated"], "truncated"),
    )


def _objects(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise ValueError("product evidence collection must be an array")
    return tuple(
        require_mapping(
            item,
            required=set(item) if isinstance(item, Mapping) else set(),
            field_name="product evidence item",
        )
        for item in value
    )


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a string array")
    return tuple(value)


def _metric_value(value: object) -> bool | int | str | None:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise ValueError("product metric value has an unsupported type")


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean")
    return value


__all__ = [
    "product_evidence_report_bytes",
    "product_evidence_report_digest",
    "product_evidence_report_from_dict",
    "product_evidence_report_to_dict",
]
