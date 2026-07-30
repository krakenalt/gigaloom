"""Read-only truthful cross-Harness handoff capsule routes."""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from gigaloom.handoff_capsules import (
    HandoffCapsuleError,
    HandoffCapsuleService,
)
from gigaloom.registry import UnknownHarnessError
from gigaloom.sessions.store import RunNotFoundError
from gigaloom.ui.async_execution import ContractAPIRouter


router = ContractAPIRouter()


@router.db_read.get("/api/runs/{run_id}/handoff-capsule")
def handoff_capsule(
    run_id: str,
    request: Request,
    target_harness_id: str = Query(min_length=1, max_length=256),
) -> dict[str, object]:
    """Build one exact capsule without starting either Harness."""
    service: HandoffCapsuleService = request.app.state.harness_handoff_capsule_service
    try:
        capsule = service.build(run_id, target_harness_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Run not found") from exc
    except UnknownHarnessError as exc:
        raise HTTPException(status_code=404, detail="Harness not found") from exc
    except HandoffCapsuleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"capsule": capsule}
