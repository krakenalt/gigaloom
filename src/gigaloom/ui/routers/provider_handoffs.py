"""Content-free provider-owned handoff previews."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from gigaloom.claude_handoff import (
    ClaudeHandoffAction,
    ClaudeHandoffError,
    ClaudeHandoffLaunchMode,
    claude_handoff_plan_to_dict,
)
from gigaloom.registry import HarnessRegistry, UnknownHarnessError
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.workspace import resolve_workspace


def create_provider_handoff_router(registry: HarnessRegistry) -> APIRouter:
    """Create provider-handoff routes over the authoritative harness registry."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/provider-handoffs/{harness_id}/preview")
    def provider_handoff_preview(
        harness_id: str,
        action: ClaudeHandoffAction,
        workspace: str = Query(default="."),
        launch_mode: ClaudeHandoffLaunchMode = Query(
            default=ClaudeHandoffLaunchMode.INTERACTIVE
        ),
    ) -> dict[str, object]:
        try:
            harness = registry.get(harness_id)
        except UnknownHarnessError as exc:
            raise HTTPException(status_code=404, detail="Harness not found") from exc
        preview = getattr(harness, "provider_handoff_preview", None)
        if not callable(preview):
            raise HTTPException(
                status_code=404,
                detail="Provider handoff is not available for this harness",
            )
        try:
            resolved_workspace = resolve_workspace(workspace)
            if resolved_workspace is None:
                raise ClaudeHandoffError("Claude handoff workspace is required")
            plan = preview(
                action=action,
                workspace=resolved_workspace,
                launch_mode=launch_mode,
            )
        except (ClaudeHandoffError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"handoff": claude_handoff_plan_to_dict(plan)}

    return router
