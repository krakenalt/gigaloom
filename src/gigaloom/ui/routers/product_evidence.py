"""Route-local on-demand product evidence projection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException, Query

from gigaloom.contracts.product_evidence_codec import (
    product_evidence_report_digest,
    product_evidence_report_to_dict,
)
from gigaloom.ui.async_execution import ContractAPIRouter


class ProductEvidenceQuery(Protocol):
    """Application boundary required by the route-local API."""

    def report(self, **kwargs: Any) -> Any: ...


def create_router(application: ProductEvidenceQuery) -> APIRouter:
    """Expose explicit local report generation without a network sink."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/evidence/product-beta")
    def product_beta_report(
        project_id: str = Query(min_length=1, max_length=256),
        since: datetime | None = Query(default=None),
        until: datetime | None = Query(default=None),
    ) -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc)
        range_end = until or generated_at
        range_start = since or range_end - timedelta(days=30)
        if range_start.tzinfo is None or range_end.tzinfo is None:
            raise HTTPException(
                status_code=400, detail="range timestamps must include a timezone"
            )
        if range_end > generated_at:
            raise HTTPException(
                status_code=400, detail="range cannot end in the future"
            )
        try:
            report = application.report(
                project_id=project_id,
                range_start=range_start,
                range_end=range_end,
                generated_at=generated_at,
            )
        except (KeyError, OSError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="product evidence cannot be generated from retained facts",
            ) from error
        return {
            "report": product_evidence_report_to_dict(report),
            "report_sha256": product_evidence_report_digest(report),
            "local_only": True,
            "uploaded": False,
        }

    return router


__all__ = ["ProductEvidenceQuery", "create_router"]
