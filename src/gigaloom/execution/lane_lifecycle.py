"""Small runner adapter for bounded lane lifecycle metadata."""

from __future__ import annotations

from typing import Any, Mapping

from gigaloom.sessions import HarnessSession, HarnessSessionStore

from .finalization import RunLaneLifecycle


def prepare_lane_run_metadata(
    lifecycle: RunLaneLifecycle | None,
    store: HarnessSessionStore,
    *,
    session: HarnessSession,
    options: Mapping[str, Any],
    payload: Mapping[str, Any],
    provider_account_binding: Mapping[str, Any] | None,
    existing_run_id: str | None = None,
) -> dict[str, Any]:
    """Freeze one bounded lane plan through the injected review owner."""
    if lifecycle is None:
        return {}
    existing_run = None
    if existing_run_id is not None:
        try:
            existing_run = store.get_run(existing_run_id)
        except KeyError:
            pass
    return lifecycle.prepare_run_metadata(
        session=session,
        options=options,
        payload=payload,
        provider_account_binding=provider_account_binding,
        existing_run=existing_run,
    )


__all__ = ["prepare_lane_run_metadata"]
