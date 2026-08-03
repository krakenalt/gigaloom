"""Route-local bounded Thread Relay list, read, send, and status APIs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query

from gigaloom.execution.thread_relay import (
    ThreadRelayAuthorizationError,
    ThreadRelayRouteActions,
    ThreadRelayTargetStateError,
    ThreadRelayUnsupportedError,
    validated_preview,
)
from gigaloom.sessions.api import (
    ThreadDeliveryConflictError,
    ThreadDeliveryNotFoundError,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.schemas.thread_relay import ThreadRelaySendRequest


ThreadSource = Literal["gigaloom", "codex", "acp"]


def create_router(actions: ThreadRelayRouteActions) -> APIRouter:
    """Create a cohesive router without mutating central app composition."""
    router = ContractAPIRouter()

    @router.fs_read.get("/api/thread-relay/threads")
    def list_threads(
        source: ThreadSource = Query(default="gigaloom"),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _mapping(
            _invoke(
                actions.list_threads,
                source=source,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.fs_read.get("/api/thread-relay/threads/{source}/{thread_id}")
    def read_thread(
        source: ThreadSource,
        thread_id: str = Path(min_length=1, max_length=256),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _mapping(
            _invoke(
                actions.read_thread,
                source=source,
                thread_id=thread_id,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.fs_read.post("/api/thread-relay/deliveries/preview")
    def preview_delivery(payload: ThreadRelaySendRequest) -> dict[str, Any]:
        request = payload.model_dump(mode="json")
        preview = _invoke(actions.preview_send, request)
        return {"dry_run": True, "preview": _invoke(validated_preview, preview)}

    @router.fs_atomic.post("/api/thread-relay/deliveries")
    def send_delivery(payload: ThreadRelaySendRequest) -> dict[str, Any]:
        request = payload.model_dump(mode="json")
        preview = _invoke(
            validated_preview,
            _invoke(actions.preview_send, request),
        )
        delivery = _invoke(
            actions.send,
            request,
            preview_digest=str(preview["preview_digest"]),
        )
        return {
            "dry_run": False,
            "preview": preview,
            "delivery": _mapping(delivery),
        }

    @router.fs_read.get("/api/thread-relay/deliveries/{delivery_id}")
    def delivery_status(
        delivery_id: str = Path(min_length=1, max_length=256),
    ) -> dict[str, Any]:
        return _mapping(_invoke(actions.status, delivery_id))

    return router


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ThreadRelayAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ThreadDeliveryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ThreadRelayTargetStateError, ThreadDeliveryConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ThreadRelayUnsupportedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return dict(value)
    return value


__all__ = ["create_router"]
