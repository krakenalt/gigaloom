"""Explicit local export for content-free product evidence."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from gigaloom.contracts.product_evidence import ProductEvidenceReportV1
from gigaloom.contracts.product_evidence_codec import product_evidence_report_bytes


def export_product_evidence_report(
    report: ProductEvidenceReportV1,
    output: str | Path,
) -> Path:
    """Atomically write one private local JSON file and return its path."""
    target = Path(output).expanduser()
    if target.exists() and not target.is_file():
        raise ValueError("product evidence output must be a file path")
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = product_evidence_report_bytes(report)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


__all__ = ["export_product_evidence_report"]
