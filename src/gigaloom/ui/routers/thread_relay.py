"""Route-local bounded Thread Relay list, read, send, and status APIs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Path, Query, Request

from gigaloom.execution.thread_relay import (
    ThreadRelayAuthorizationError,
    LOCAL_THREAD_ACTOR_SCOPE,
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
ThreadRelayActionsFactory = Callable[[str, str], ThreadRelayRouteActions]


def create_router(
    actions: ThreadRelayRouteActions | None = None,
    *,
    actions_factory: ThreadRelayActionsFactory | None = None,
) -> APIRouter:
    """Create a cohesive router without mutating central app composition."""
    if (actions is None) == (actions_factory is None):
        raise ValueError("exactly one Thread Relay action source is required")
    router = ContractAPIRouter()

    @router.fs_read.get("/api/thread-relay/threads")
    def list_threads(
        request: Request,
        source: ThreadSource = Query(default="gigaloom"),
        project_id: str = Query(min_length=1, max_length=256),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _mapping(
            _invoke(
                _actions(actions, actions_factory, request, project_id).list_threads,
                source=source,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.fs_read.get("/api/thread-relay/threads/{source}/{thread_id}")
    def read_thread(
        request: Request,
        source: ThreadSource,
        thread_id: str = Path(min_length=1, max_length=256),
        project_id: str = Query(min_length=1, max_length=256),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _mapping(
            _invoke(
                _actions(actions, actions_factory, request, project_id).read_thread,
                source=source,
                thread_id=thread_id,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.fs_read.get("/api/thread-relay/threads/{source}/{thread_id}/deliveries")
    def list_deliveries(
        request: Request,
        source: ThreadSource,
        thread_id: str = Path(min_length=1, max_length=256),
        project_id: str = Query(min_length=1, max_length=256),
        direction: Literal["incoming", "outgoing"] = Query(),
        cursor: str | None = Query(default=None, max_length=1024),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> dict[str, Any]:
        return _mapping(
            _invoke(
                _actions(
                    actions,
                    actions_factory,
                    request,
                    project_id,
                ).list_deliveries,
                source=source,
                thread_id=thread_id,
                direction=direction,
                cursor=cursor,
                limit=limit,
            )
        )

    @router.fs_read.post("/api/thread-relay/deliveries/preview")
    def preview_delivery(
        request: Request,
        payload: ThreadRelaySendRequest,
    ) -> dict[str, Any]:
        request_payload = payload.model_dump(mode="json")
        action_service = _actions(
            actions,
            actions_factory,
            request_scope=request,
            project_id=payload.project_id,
        )
        preview = _invoke(action_service.preview_send, request_payload)
        return {"dry_run": True, "preview": _invoke(validated_preview, preview)}

    @router.fs_atomic.post("/api/thread-relay/deliveries")
    def send_delivery(
        request: Request,
        payload: ThreadRelaySendRequest,
    ) -> dict[str, Any]:
        action_service = _actions(
            actions,
            actions_factory,
            request,
            payload.project_id,
        )
        request_payload = payload.model_dump(mode="json")
        preview = _invoke(
            validated_preview,
            _invoke(action_service.preview_send, request_payload),
        )
        delivery = _invoke(
            action_service.send,
            request_payload,
            preview_digest=str(preview["preview_digest"]),
        )
        return {
            "dry_run": False,
            "preview": preview,
            "delivery": _mapping(delivery),
        }

    @router.fs_read.get("/api/thread-relay/deliveries/{delivery_id}")
    def delivery_status(
        request: Request,
        delivery_id: str = Path(min_length=1, max_length=256),
        project_id: str = Query(min_length=1, max_length=256),
    ) -> dict[str, Any]:
        action_service = _actions(
            actions,
            actions_factory,
            request,
            project_id,
        )
        return _mapping(_invoke(action_service.status, delivery_id))

    return router


def _actions(
    fixed: ThreadRelayRouteActions | None,
    factory: ThreadRelayActionsFactory | None,
    request_scope: Request,
    project_id: str,
) -> ThreadRelayRouteActions:
    if fixed is not None:
        return fixed
    assert factory is not None
    actor = getattr(request_scope.state, "ui_actor", None)
    actor_id = actor.get("actor_id") if isinstance(actor, Mapping) else None
    if not isinstance(actor_id, str) or not actor_id:
        actor_id = LOCAL_THREAD_ACTOR_SCOPE
    return factory(actor_id, project_id)


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
