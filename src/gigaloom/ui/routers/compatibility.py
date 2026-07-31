"""Read-only compatibility guardian routes."""

from __future__ import annotations

from typing import Any

from fastapi import Query, Request

from gigaloom.diagnostics.compatibility.guardian import (
    run_compatibility_guardian,
)
from gigaloom.ui.async_execution import ContractAPIRouter


router = ContractAPIRouter()


@router.proc_read.get("/api/compatibility/guardian")
def compatibility_guardian(
    request: Request,
    harness: list[str] | None = Query(default=None),
) -> dict[str, Any]:
    """Run bounded offline fixtures without starting providers or integrations."""
    return run_compatibility_guardian(
        request.app.state.harness_registry,
        harness_ids=tuple(harness) if harness else None,
    )
