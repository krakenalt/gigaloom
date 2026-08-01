"""Read-only state-integrity projection for the operator Web surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from gigaloom.diagnostics.api import (
    RecoveryCheckService,
    recovery_scan_report_to_dict,
)
from gigaloom.ui.async_execution import ContractAPIRouter


def create_router(
    checks: RecoveryCheckService,
    *,
    data_root: str | Path,
) -> APIRouter:
    """Create an on-demand check for the configured backend-owned data root."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/reliability")
    def check_state() -> dict[str, Any]:
        try:
            report = checks.check(data_root)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=409,
                detail="configured state cannot be validated",
            ) from error
        return recovery_scan_report_to_dict(report)

    return router


__all__ = ["create_router"]
