from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from gigaloom.contracts.product_evidence import (
    ProductEvidenceReportV1,
    ProductEvidenceSourceV1,
    ProductMetricStatus,
    ProductMetricUnit,
    ProductMetricV1,
)
from gigaloom.contracts.product_evidence_codec import (
    product_evidence_report_bytes,
    product_evidence_report_digest,
    product_evidence_report_from_dict,
    product_evidence_report_to_dict,
)


NOW = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)


def _report() -> ProductEvidenceReportV1:
    return ProductEvidenceReportV1(
        report_id="product-beta-demo",
        project_id="project-demo",
        range_start=NOW - timedelta(days=7),
        range_end=NOW,
        generated_at=NOW,
        metrics=(
            ProductMetricV1(
                metric_id="runs.success.count",
                status=ProductMetricStatus.OBSERVED,
                unit=ProductMetricUnit.COUNT,
                value=3,
                reason_code="retained_owner_facts",
                sample_size=5,
            ),
            ProductMetricV1(
                metric_id="gateway.preflight.success_ratio",
                status=ProductMetricStatus.UNKNOWN,
                unit=ProductMetricUnit.RATIO_BASIS_POINTS,
                value=None,
                reason_code="source_not_persisted",
                sample_size=0,
            ),
        ),
        sources=(
            ProductEvidenceSourceV1(
                source_id="sessions",
                observed_count=5,
                truncated=False,
            ),
        ),
        omissions=("gateway.preflight",),
    )


def test_product_evidence_round_trips_as_deterministic_content_free_json() -> None:
    report = _report()

    encoded = product_evidence_report_bytes(report)
    decoded = product_evidence_report_from_dict(product_evidence_report_to_dict(report))

    assert decoded == report
    assert encoded == product_evidence_report_bytes(decoded)
    assert product_evidence_report_digest(report) == product_evidence_report_digest(
        decoded
    )
    assert b"prompt" not in encoded
    assert b"response" not in encoded
    assert b"network" not in encoded


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("content_free", False, "content-free"),
        ("local_only", False, "local-only"),
        ("range_start", NOW - timedelta(days=367), "bounded maximum"),
    ],
)
def test_product_evidence_rejects_non_local_or_unbounded_reports(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_report(), **{field: value})


def test_unknown_metric_cannot_claim_a_value() -> None:
    with pytest.raises(ValueError, match="unknown product metric"):
        ProductMetricV1(
            metric_id="metric.unknown",
            status=ProductMetricStatus.UNKNOWN,
            unit=ProductMetricUnit.COUNT,
            value=1,
            reason_code="source_missing",
            sample_size=1,
        )
