"""Provider-neutral Workbench snapshot and delta API."""

from __future__ import annotations

from fastapi import Query

from gpt2giga_harness.ui.async_execution import ContractAPIRouter
from gpt2giga_harness.ui.dependencies import AppServicesDependency
from gpt2giga_harness.workbench_protocol import workbench_state_page_to_dict


router = ContractAPIRouter()


@router.loop_read.get("/api/workbench/state")
async def workbench_state(
    services: AppServicesDependency,
    cursor: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=32, ge=1, le=32),
) -> dict[str, object]:
    """Return one bounded authoritative snapshot plus ordered reconnect deltas."""
    page = services.workbench_backbone.read(cursor, limit=limit)
    return workbench_state_page_to_dict(page)
