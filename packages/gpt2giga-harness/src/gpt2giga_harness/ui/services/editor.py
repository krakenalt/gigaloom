"""Editor request policy helpers owned by the UI application layer."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException, Request


def editor_dry_run(request: Request, payload: Mapping[str, Any]) -> bool:
    """Keep remote UI identities from crossing the local process boundary."""
    dry_run = bool(payload.get("dry_run", False))
    if getattr(request.state, "ui_actor", None) is not None and not dry_run:
        raise HTTPException(
            status_code=403,
            detail="Remote editor execution is disabled",
        )
    return dry_run
