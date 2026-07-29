"""Domain router extracted from the FastAPI composition root."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from gpt2giga_harness.native.base import discovery_error_to_dict
from gpt2giga_harness.native.models import NativeSessionStatus
from gpt2giga_harness.native.store import native_session_ref_to_dict
from gpt2giga_harness.session_titles import provider_native_title_metadata
from gpt2giga_harness.sessions import SessionNotFoundError
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessNativeLink,
    HarnessStoredEvent,
    message_to_dict,
    native_link_to_dict,
)
from gpt2giga_harness.sessions.redaction import redact_for_storage
from gpt2giga_harness.sessions.store import new_id, utc_now
from gpt2giga_harness.types import parse_api_mode
from gpt2giga_harness.ui.async_execution import ConformantAPIRoute
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.services.native_process_metadata import (
    _native_snapshot_link_metadata,
)
from gpt2giga_harness.ui.services.native_sessions import (
    _filter_external_native_refs,
    _native_connector_or_404,
    _native_discovered_project_id,
    _native_discovery_limit,
    _native_import_message_role,
    _native_import_session_metadata,
    _native_project_id,
    _native_ref_or_404,
    _native_transcript_message_to_dict,
    _redacted_mapping,
)
from gpt2giga_harness.ui.services.navigation import session_summary as _session_summary
from gpt2giga_harness.ui.services.request_values import (
    optional_text as _optional_text,
)
from gpt2giga_harness.ui.services.request_values import (
    required_text as _required_text,
)
from gpt2giga_harness.workspace import resolve_workspace


def create_router(services: AppServices) -> APIRouter:
    """Create the native domain router with typed application services."""
    router = APIRouter(route_class=ConformantAPIRoute)

    @router.get("/api/native/sessions")
    def native_sessions(
        harness_id: str | None = Query(default=None),
        workspace: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        include_external: bool = Query(default=False),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        resolved_workspace = resolve_workspace(_optional_text(workspace))
        resolved_project_id = _native_project_id(
            project_id=_optional_text(project_id),
            workspace=resolved_workspace,
            data_dir=services.config.data_dir,
        )
        refs = services.native_index_store.list_refs(
            harness_id=_optional_text(harness_id),
            workspace=resolved_workspace,
            project_id=resolved_project_id,
            limit=limit,
        )
        refs = _filter_external_native_refs(refs, include_external=include_external)
        return {"sessions": [native_session_ref_to_dict(ref) for ref in refs]}

    @router.post("/api/native/sessions/sync")
    def native_sessions_sync(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        resolved_workspace = resolve_workspace(_optional_text(payload.get("workspace")))
        resolved_project_id = _native_project_id(
            project_id=_optional_text(payload.get("project_id")),
            workspace=resolved_workspace,
            data_dir=services.config.data_dir,
        )
        try:
            result = services.native_registry.discover(
                harness_id=_optional_text(payload.get("harness_id")),
                workspace=resolved_workspace,
                include_external=bool(payload.get("include_external")),
                cursor=_optional_text(payload.get("cursor")),
                limit=_native_discovery_limit(payload.get("limit")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        stored = [
            services.native_index_store.upsert_ref(
                ref,
                project_id=_native_discovered_project_id(
                    ref, workspace=resolved_workspace, project_id=resolved_project_id
                ),
            )
            for ref in result.sessions
        ]
        return {
            "sessions": [native_session_ref_to_dict(ref) for ref in stored],
            "errors": [discovery_error_to_dict(error) for error in result.errors],
            "next_cursor": result.next_cursor,
            "scanned_count": result.scanned_count,
        }

    @router.get("/api/native/sessions/{native_ref_id}/preview")
    def native_session_preview(
        native_ref_id: str, max_messages: int = Query(default=20, ge=1, le=100)
    ) -> dict[str, Any]:
        ref = _native_ref_or_404(services.native_index_store, native_ref_id)
        connector = _native_connector_or_404(services.native_registry, ref.harness_id)
        messages = connector.preview(ref, max_messages=max_messages)
        return {
            "ref": native_session_ref_to_dict(ref),
            "messages": [
                _native_transcript_message_to_dict(message) for message in messages
            ],
        }

    @router.post("/api/native/sessions/{native_ref_id}/import")
    def native_session_import(native_ref_id: str) -> dict[str, Any]:
        ref = _native_ref_or_404(services.native_index_store, native_ref_id)
        if not ref.can_import:
            raise HTTPException(
                status_code=400, detail="Native session cannot be imported"
            )
        connector = _native_connector_or_404(services.native_registry, ref.harness_id)
        imported = connector.import_ref(ref)
        if not imported:
            raise HTTPException(
                status_code=400, detail="Native session has no importable messages"
            )
        session = services.session_store.create_session(
            title=str(redact_for_storage(ref.title)),
            workspace=ref.workspace,
            default_harness_id=ref.harness_id,
            default_model=ref.execution_snapshot.model
            if ref.execution_snapshot is not None
            else _optional_text(ref.metadata.get("model")),
            default_api_mode=parse_api_mode(
                ref.execution_snapshot.api_mode
                if ref.execution_snapshot is not None
                else services.config.default_api_mode
            ),
            default_mode="plan",
            native={
                "source": "native_import",
                "native_ref_id": ref.id,
                "native_session_id": ref.native_session_id,
                "status": ref.status.value,
            },
            metadata=provider_native_title_metadata(
                _native_import_session_metadata(ref),
                provider=ref.harness_id,
                source_id=ref.native_session_id or ref.id,
            ),
        )
        messages = []
        skipped_count = 0
        for message in imported:
            role = _native_import_message_role(message.role)
            if role is None:
                skipped_count += 1
                services.session_store.append_event(
                    HarnessStoredEvent(
                        id=new_id("evt"),
                        session_id=session.id,
                        run_id="native_import",
                        type="native_import_warning",
                        message="Skipped native transcript item with unknown role.",
                        payload={
                            "native_ref_id": ref.id,
                            "native_session_id": ref.native_session_id,
                            "role": message.role,
                            "metadata": _redacted_mapping(message.metadata),
                        },
                        created_at=message.created_at or utc_now(),
                    )
                )
                continue
            messages.append(
                services.session_store.append_message(
                    HarnessMessage(
                        id=new_id("msg"),
                        session_id=session.id,
                        run_id=None,
                        role=role,
                        content=str(redact_for_storage(message.content)),
                        created_at=message.created_at or utc_now(),
                        harness_id=ref.harness_id,
                        metadata={
                            "source": "native_import",
                            "native_ref_id": ref.id,
                            "native_session_id": ref.native_session_id,
                            **dict(redact_for_storage(dict(message.metadata))),
                        },
                    )
                )
            )
        link = services.session_store.append_native_link(
            session.id,
            HarnessNativeLink(
                id=new_id("nlink"),
                session_id=session.id,
                harness_id=ref.harness_id,
                status=NativeSessionStatus.IMPORTED,
                created_at=utc_now(),
                updated_at=utc_now(),
                native_session_id=ref.native_session_id,
                native_ref_id=ref.id,
                source=ref.source,
                workspace=ref.workspace,
                metadata={
                    "source_status": ref.status.value,
                    "imported_message_count": len(messages),
                    "skipped_item_count": skipped_count,
                    "project_id": ref.metadata.get("project_id"),
                    **_native_snapshot_link_metadata(ref),
                },
            ),
        )
        return {
            "session": _session_summary(services.session_store, session.id),
            "messages": [message_to_dict(message) for message in messages],
            "native_link": native_link_to_dict(link),
        }

    @router.post("/api/sessions/{session_id}/native/link")
    def native_session_link(
        session_id: str, payload: dict[str, Any] = Body(default_factory=dict)
    ) -> dict[str, Any]:
        try:
            session = services.session_store.get_session(session_id)
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        ref = _native_ref_or_404(
            services.native_index_store,
            _required_text(payload.get("native_ref_id"), "native_ref_id is required"),
        )
        link = services.session_store.append_native_link(
            session.id,
            HarnessNativeLink(
                id=new_id("nlink"),
                session_id=session.id,
                harness_id=ref.harness_id,
                status=NativeSessionStatus.LINKED,
                created_at=utc_now(),
                updated_at=utc_now(),
                native_session_id=ref.native_session_id,
                native_ref_id=ref.id,
                source=ref.source,
                workspace=ref.workspace,
                metadata={
                    "source_status": ref.status.value,
                    "project_id": ref.metadata.get("project_id"),
                    "can_resume": ref.can_resume,
                    "resume_reason": ref.resume_reason,
                    **_native_snapshot_link_metadata(ref),
                },
            ),
        )
        return {
            "session": _session_summary(services.session_store, session.id),
            "native_link": native_link_to_dict(link),
        }

    return router
