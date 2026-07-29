"""Routes extracted from the FastAPI composition root: session catalog."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from fastapi import Body, HTTPException, Query
from gpt2giga_harness.ui.services.navigation import (
    cache_navigation_response as _cache_navigation_response,
    fork_session_from_session as _fork_session_from_session,
    navigation_cached as _navigation_cached,
    navigation_export_text as _navigation_export_text,
    navigation_message_preview as _navigation_message_preview,
    session_patch as _session_patch,
    session_summary as _session_summary,
    validate_navigation_binding as _validate_navigation_binding,
)
from gpt2giga_harness.ui.services.request_values import (
    optional_text as _optional_text,
    required_text as _required_text,
)
from gpt2giga_harness.ui.services.session_queries import (
    MAX_UI_MESSAGES,
    has_older_messages as _has_older_messages,
    list_session_window as _list_session_window,
    recent_messages as _recent_messages,
)
from gpt2giga_harness.ui.async_execution import ConformantAPIRoute
from gpt2giga_harness.session_exports import write_session_export
from gpt2giga_harness.sessions import SessionNotFoundError
from gpt2giga_harness.sessions.models import bundle_to_dict
from gpt2giga_harness.workspace import resolve_workspace
from fastapi import APIRouter
from gpt2giga_harness.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = APIRouter(route_class=ConformantAPIRoute)

    @router.get("/api/sessions")
    def sessions(
        project_id: str | None = Query(default=None),
        workspace: str | None = Query(default=None),
        harness_id: str | None = Query(default=None),
        q: str | None = Query(default=None),
        include_archived: bool = Query(default=False),
        include_arena: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        resolved_workspace = resolve_workspace(_optional_text(workspace))
        arenas = services.arena_store.list(workspace=resolved_workspace)
        excluded_session_ids = {
            child.session_id
            for arena in arenas
            for child in arena.child_runs
            if child.session_id is not None
        }
        if not include_arena:
            excluded_session_ids.update(arena.session_id for arena in arenas)
        items = _list_session_window(
            services.session_store,
            project_id=_optional_text(project_id),
            workspace=resolved_workspace,
            harness_id=_optional_text(harness_id),
            q=_optional_text(q),
            include_archived=include_archived,
            limit=limit,
            excluded_ids=excluded_session_ids,
        )
        return {
            "sessions": [
                _session_summary(services.session_store, session.id)
                for session in items
            ]
        }

    @router.post("/api/sessions")
    def create_session(payload: dict[str, Any] = Body(default_factory=dict)):
        try:
            session = services.session_service.create_session(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"session": _session_summary(services.session_store, session.id)}

    @router.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            return bundle_to_dict(services.session_store.get_session_bundle(session_id))
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc

    @router.patch("/api/sessions/{session_id}")
    def update_session(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            current = services.session_store.get_session(session_id)
            patch = _session_patch(payload, session=current)
            session = services.session_store.update_session(session_id, **patch)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"session": _session_summary(services.session_store, session.id)}

    @router.get("/api/sessions/{session_id}/navigation-preview")
    def session_navigation_preview(
        session_id: str, q: str | None = Query(default=None)
    ) -> dict[str, Any]:
        try:
            session = services.session_store.get_session(session_id)
            messages = _recent_messages(
                services.session_store,
                session_id,
                limit=MAX_UI_MESSAGES,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        needle = (_optional_text(q) or "").casefold()
        matches = [
            item for item in messages if not needle or needle in item.content.casefold()
        ]
        selected = matches[-100:]
        return {
            "session": _session_summary(services.session_store, session.id),
            "transcript": [_navigation_message_preview(item) for item in selected],
            "match_count": len(matches),
            "truncated": len(matches) > len(selected)
            or _has_older_messages(services.session_store, session_id, messages),
        }

    @router.post("/api/sessions/{session_id}/navigation-update")
    def session_navigation_update(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        cached = _navigation_cached(
            services.session_navigation_mutations, payload, "update"
        )
        if cached is not None:
            return cached
        _validate_navigation_binding(services.session_store, session_id, payload)
        try:
            current = services.session_store.get_session(session_id)
            patch = _session_patch(payload, session=current)
            updated = services.session_store.update_session_if_revision(
                session_id,
                _required_text(payload.get("session_revision"), "session_revision"),
                **patch,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        if updated is None:
            raise HTTPException(
                status_code=409,
                detail="Session changed; authoritative resnapshot required",
            )
        response = {"session": _session_summary(services.session_store, updated.id)}
        _cache_navigation_response(
            services.session_navigation_mutations, payload, response, "update"
        )
        return response

    @router.post("/api/sessions/{session_id}/navigation-delete")
    def session_navigation_delete(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        cached = _navigation_cached(
            services.session_navigation_mutations, payload, "delete"
        )
        if cached is not None:
            return cached
        summary = _validate_navigation_binding(
            services.session_store, session_id, payload
        )
        if summary.get("session_lease") is not None:
            raise HTTPException(
                status_code=409, detail="Active session lease blocks destructive action"
            )
        if not services.session_store.delete_session_if_revision(
            session_id,
            _required_text(payload.get("session_revision"), "session_revision"),
        ):
            raise HTTPException(
                status_code=409,
                detail="Session changed; authoritative resnapshot required",
            )
        response = {"deleted": True}
        _cache_navigation_response(
            services.session_navigation_mutations, payload, response, "delete"
        )
        return response

    @router.post("/api/sessions/{session_id}/navigation-fork")
    def session_navigation_fork(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        cached = _navigation_cached(
            services.session_navigation_mutations, payload, "fork"
        )
        if cached is not None:
            return cached
        _validate_navigation_binding(services.session_store, session_id, payload)
        fork = _fork_session_from_session(services.session_store, session_id)
        response = {"session": _session_summary(services.session_store, fork.id)}
        _cache_navigation_response(
            services.session_navigation_mutations, payload, response, "fork"
        )
        return response

    @router.post("/api/sessions/{session_id}/navigation-export")
    def session_navigation_export(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        cached = _navigation_cached(
            services.session_navigation_mutations, payload, "export"
        )
        if cached is not None:
            return cached
        _validate_navigation_binding(services.session_store, session_id, payload)
        session = services.session_store.get_session(session_id)
        messages = services.session_store.list_messages(session_id)
        path = write_session_export(
            Path(services.config.data_dir) / "exports",
            _navigation_export_text(session, messages),
        )
        response = {"export": {"path": str(path), "message_count": len(messages)}}
        _cache_navigation_response(
            services.session_navigation_mutations, payload, response, "export"
        )
        return response

    @router.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        try:
            services.session_store.delete_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {"deleted": True}

    return router
