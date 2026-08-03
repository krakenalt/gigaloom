"""Attachments domain routes."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import Response

from gigaloom.attachments import (
    AttachmentNotFoundError,
    AttachmentSessionNotFoundError,
    AttachmentValidationError,
    TextAttachmentDecodeError,
    charset_evidence_for,
    charset_evidence_to_dict,
    decode_attachment_text,
    decode_attachment_text_prefix,
    failed_charset_evidence,
    truncate_utf8_text,
)
from gigaloom.attachments.limits import normalize_workspace_file
from gigaloom.project import resolve_project
from gigaloom.sessions import (
    SessionNotFoundError,
)
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.attachments import (
    attachment_limits as _attachment_limits,
)
from gigaloom.ui.services.attachments import (
    attachment_response as _attachment_response,
)
from gigaloom.ui.services.attachments import (
    attachment_workspace as _attachment_workspace,
)
from gigaloom.ui.services.attachments import (
    content_disposition as _content_disposition,
)
from gigaloom.ui.services.attachments import (
    decode_attachment_payload as _decode_attachment_payload,
)
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.attachments import (
    session_project_id as _session_project_id,
)
from gigaloom.ui.services.attachments import (
    workspace_api_root as _workspace_api_root,
)
from gigaloom.ui.services.attachments import (
    workspace_limits as _workspace_limits,
)
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.services.request_values import required_text as _required_text
from gigaloom.workspace import (
    workspace_file_metadata,
    workspace_tree,
)

TUI_FILE_PREVIEW_BYTES = 8 * 1024
TUI_FILE_PREVIEW_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
INLINE_ATTACHMENT_MIME_TYPES = frozenset(
    {"image/gif", "image/jpeg", "image/png", "image/webp"}
)


def create_router(services: AppServices) -> APIRouter:
    """Create the attachments router."""
    router = ContractAPIRouter()

    @router.fs_atomic.post("/api/sessions/{session_id}/attachments")
    def create_attachment(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            session = services.session_store.get_session(session_id)
            attachment = services.attachment_store.create_upload(
                session_id=session.id,
                project_id=_session_project_id(session),
                filename=str(payload.get("filename") or ""),
                data=_decode_attachment_payload(payload.get("data_base64")),
                mime_type=_optional_text(payload.get("mime_type")),
                source=_optional_text(payload.get("source")) or "upload",
                metadata=_metadata_mapping(payload.get("metadata")),
                limits=_attachment_limits(session),
            )
        except (SessionNotFoundError, AttachmentSessionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"attachment": _attachment_response(services.registry, attachment)}

    @router.fs_atomic.post("/api/sessions/{session_id}/attachments/workspace")
    def create_workspace_attachment(
        session_id: str, payload: dict[str, Any] = Body(...)
    ) -> dict[str, Any]:
        try:
            session = services.session_store.get_session(session_id)
            workspace_root = _attachment_workspace(session, payload)
            attachment = services.attachment_store.create_workspace_reference(
                session_id=session.id,
                project_id=_session_project_id(session)
                or resolve_project(
                    workspace_root, data_dir=services.config.data_dir
                ).id,
                workspace_root=workspace_root,
                path=_required_text(payload.get("path"), "path is required"),
                mime_type=_optional_text(payload.get("mime_type")),
                metadata=_metadata_mapping(payload.get("metadata")),
                limits=_attachment_limits(session, workspace_root=workspace_root),
            )
        except (SessionNotFoundError, AttachmentSessionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"attachment": _attachment_response(services.registry, attachment)}

    @router.fs_read.get("/api/sessions/{session_id}/attachments/workspace/search")
    def search_session_workspace_attachments(
        session_id: str,
        q: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=50),
    ) -> dict[str, Any]:
        """Return bounded safe attachment candidates for one session workspace."""
        try:
            session = services.session_store.get_session(session_id)
            workspace_root = _attachment_workspace(session, {})
            files = workspace_tree(
                workspace_root,
                query=q,
                limits=_attachment_limits(session, workspace_root=workspace_root),
                result_limit=limit,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"q": _optional_text(q) or "", "files": files, "bounded": True}

    @router.fs_read.get("/api/sessions/{session_id}/attachments/workspace/preview")
    def preview_session_workspace_attachment(
        session_id: str, path: str = Query(min_length=1)
    ) -> dict[str, Any]:
        """Return a bounded terminal-safe text preview for one safe candidate."""
        try:
            session = services.session_store.get_session(session_id)
            workspace_root = _attachment_workspace(session, {})
            limits = _attachment_limits(session, workspace_root=workspace_root)
            metadata = workspace_file_metadata(workspace_root, path, limits=limits)
            preview = {
                "status": "unsupported",
                "text": "Preview is unavailable for this file type.",
                "truncated": False,
            }
            if metadata["kind"] == "text":
                resolved, _relative = normalize_workspace_file(
                    workspace_root, path, limits
                )
                with resolved.open("rb") as handle:
                    raw = handle.read(TUI_FILE_PREVIEW_BYTES + 8)
                source_truncated = metadata["size_bytes"] > len(raw)
                with resolved.open("rb") as handle:
                    source_digest = hashlib.file_digest(handle, "sha256").hexdigest()
                try:
                    decoded = (
                        decode_attachment_text_prefix(raw)
                        if source_truncated
                        else decode_attachment_text(raw)
                    )
                except TextAttachmentDecodeError as exc:
                    evidence = failed_charset_evidence(
                        raw,
                        exc.failure_reason,
                        truncated=source_truncated,
                        source_digest=source_digest,
                    )
                    preview = {
                        "status": "rejected",
                        "text": "",
                        "truncated": source_truncated,
                        "charset_evidence": charset_evidence_to_dict(evidence),
                    }
                else:
                    text, output_truncated = truncate_utf8_text(
                        decoded.text, TUI_FILE_PREVIEW_BYTES
                    )
                    truncated = source_truncated or output_truncated
                    evidence = charset_evidence_for(
                        raw,
                        decoded,
                        truncated=truncated,
                        source_digest=source_digest,
                    )
                    preview = {
                        "status": "truncated" if truncated else "ready",
                        "text": TUI_FILE_PREVIEW_CONTROL_RE.sub("�", text).replace(
                            "\r", ""
                        ),
                        "truncated": truncated,
                        "charset_evidence": charset_evidence_to_dict(evidence),
                    }
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except (AttachmentValidationError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"file": metadata, "preview": preview, "bounded": True}

    @router.fs_read.get("/api/sessions/{session_id}/attachments")
    def session_attachments(session_id: str) -> dict[str, Any]:
        try:
            services.session_store.get_session(session_id)
            attachments = services.attachment_store.list_session_attachments(session_id)
        except (SessionNotFoundError, AttachmentSessionNotFoundError) as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        return {
            "attachments": [
                _attachment_response(services.registry, attachment)
                for attachment in attachments
            ]
        }

    @router.fs_read.get("/api/attachments/{attachment_id}/metadata")
    def attachment_metadata(attachment_id: str) -> dict[str, Any]:
        try:
            attachment = services.attachment_store.get_attachment(attachment_id)
        except AttachmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment not found") from exc
        return {"attachment": _attachment_response(services.registry, attachment)}

    @router.fs_read.get("/api/attachments/{attachment_id}")
    def attachment_blob(attachment_id: str) -> Response:
        try:
            attachment = services.attachment_store.get_attachment(attachment_id)
            if attachment.storage_path:
                data = services.attachment_store.read_blob(attachment_id)
            elif attachment.workspace_path and attachment.mime_type.startswith(
                "image/"
            ):
                session = services.session_store.get_session(attachment.session_id)
                workspace_root = _attachment_workspace(session, {})
                resolved, _relative = normalize_workspace_file(
                    workspace_root,
                    attachment.workspace_path,
                    _attachment_limits(session, workspace_root=workspace_root),
                )
                data = resolved.read_bytes()
            else:
                raise AttachmentValidationError("Attachment has no stored blob")
        except AttachmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment not found") from exc
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail="Attachment content is unavailable"
            ) from exc
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        inline = attachment.mime_type.lower() in INLINE_ATTACHMENT_MIME_TYPES
        return Response(
            content=data,
            media_type=(attachment.mime_type if inline else "application/octet-stream"),
            headers={
                "Content-Disposition": _content_disposition(
                    attachment.filename, inline=inline
                ),
                "X-GPT2GIGA-Attachment-Id": attachment.id,
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.fs_atomic.delete("/api/attachments/{attachment_id}")
    def delete_attachment(attachment_id: str) -> dict[str, Any]:
        try:
            services.attachment_store.delete_attachment(attachment_id)
        except AttachmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment not found") from exc
        return {"deleted": True}

    @router.fs_read.get("/api/workspace/tree")
    def workspace_tree_endpoint(
        workspace: str | None = Query(default=None),
        q: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            workspace_root = _workspace_api_root(workspace, services.config.data_dir)
            files = workspace_tree(
                workspace_root,
                query=q,
                limits=_workspace_limits(workspace_root),
                result_limit=limit,
            )
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "workspace": workspace_root,
            "q": _optional_text(q) or "",
            "files": files,
        }

    @router.fs_read.get("/api/workspace/file/metadata")
    def workspace_file_metadata_endpoint(
        workspace: str | None = Query(default=None), path: str = Query(...)
    ) -> dict[str, Any]:
        try:
            workspace_root = _workspace_api_root(workspace, services.config.data_dir)
            metadata = workspace_file_metadata(
                workspace_root, path, limits=_workspace_limits(workspace_root)
            )
        except (AttachmentValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"workspace": workspace_root, "file": metadata}

    return router
