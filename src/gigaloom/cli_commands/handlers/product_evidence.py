"""CLI handler for explicit local product beta evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import sys

from gigaloom.application.product_evidence import ProductEvidenceApplication
from gigaloom.config import HarnessConfig
from gigaloom.contracts.product_evidence_codec import (
    product_evidence_report_digest,
    product_evidence_report_to_dict,
)
from gigaloom.review.product_evidence import export_product_evidence_report


def _handle_product_beta_evidence(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> int:
    """Generate one report only after an explicit command invocation."""
    generated_at = datetime.now(timezone.utc)
    range_end = _timestamp(args.until, "until") if args.until else generated_at
    range_start = (
        _timestamp(args.since, "since")
        if args.since
        else range_end - timedelta(days=30)
    )
    if range_end > generated_at:
        raise ValueError("product evidence range cannot end in the future")
    application = ProductEvidenceApplication.from_data_dir(config.data_dir)
    report = application.report(
        project_id=args.project_id,
        range_start=range_start,
        range_end=range_end,
        generated_at=generated_at,
    )
    output = export_product_evidence_report(report, args.output)
    result = {
        "report": product_evidence_report_to_dict(report),
        "report_sha256": product_evidence_report_digest(report),
        "output": str(output),
        "uploaded": False,
    }
    if args.json:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        sys.stdout.write(
            f"Product beta evidence: {report.report_id}\n"
            f"Output: {output}\n"
            "Local content-free export complete; nothing was uploaded.\n"
        )
    return 0


def _timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"product evidence {field_name} is not ISO 8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"product evidence {field_name} must include a timezone")
    return parsed


__all__ = ["_handle_product_beta_evidence"]
