"""Read-only projection of immutable upgrade comparison reports."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from gigaloom.diagnostics.api import (
    UpgradeRadarReportStore,
    upgrade_radar_report_list_to_dict,
)
from gigaloom.ui.async_execution import ContractAPIRouter


def create_router(store: UpgradeRadarReportStore) -> APIRouter:
    """Expose bounded report summaries without candidate execution authority."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/upgrade-radar")
    def list_reports() -> dict[str, Any]:
        try:
            reports = store.list()
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="upgrade comparison evidence cannot be read",
            ) from error
        return upgrade_radar_report_list_to_dict(reports)

    return router


__all__ = ["create_router"]
