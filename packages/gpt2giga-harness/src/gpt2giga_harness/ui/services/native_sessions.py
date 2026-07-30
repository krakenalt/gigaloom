"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException

from gpt2giga_harness.native.discovery import normalize_native_workspace
from gpt2giga_harness.native.models import (
    NativeSessionRef,
    NativeSessionStatus,
    NativeTranscriptMessage,
)
from gpt2giga_harness.native.registry import (
    NativeHistoryConnectorRegistry,
    UnknownNativeHistoryConnectorError,
)
from gpt2giga_harness.native.store import NativeSessionIndexStore
from gpt2giga_harness.project import resolve_project
from gpt2giga_harness.sessions.redaction import redact_for_storage
from gpt2giga_harness.ui.services.native_process_metadata import (
    _native_snapshot_link_metadata,
)
from gpt2giga_harness.ui.services.request_values import optional_text as _optional_text


def _native_project_id(
    *, project_id: str | None, workspace: str | None, data_dir: str
) -> str | None:
    if project_id is not None:
        return project_id
    if workspace is None:
        return None
    return resolve_project(workspace, data_dir=data_dir).id


def _native_discovery_limit(value: Any) -> int:
    if value is None:
        return 100
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="limit must be an integer") from exc
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    return limit


def _native_discovered_project_id(
    ref: NativeSessionRef, *, workspace: str | None, project_id: str | None
) -> str | None:
    """Apply request scope only when the discovered ref proves the same workspace."""
    if (
        project_id is not None
        and normalize_native_workspace(ref.workspace)
        == normalize_native_workspace(workspace)
        and (ref.workspace is not None)
    ):
        return project_id
    return _optional_text(ref.metadata.get("project_id"))


def _filter_external_native_refs(
    refs: tuple[NativeSessionRef, ...], *, include_external: bool
) -> tuple[NativeSessionRef, ...]:
    if include_external:
        return refs
    external_statuses = {
        NativeSessionStatus.EXTERNAL_NATIVE,
        NativeSessionStatus.READONLY,
    }
    return tuple((ref for ref in refs if ref.status not in external_statuses))


def _native_ref_or_404(
    native_index_store: NativeSessionIndexStore, native_ref_id: str
) -> NativeSessionRef:
    ref = native_index_store.get_ref(native_ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="Native session not found")
    return ref


def _native_connector_or_404(
    native_registry: NativeHistoryConnectorRegistry, harness_id: str
):
    try:
        return native_registry.get(harness_id)
    except UnknownNativeHistoryConnectorError as exc:
        raise HTTPException(
            status_code=404, detail="Native connector not found"
        ) from exc


def _native_transcript_message_to_dict(
    message: NativeTranscriptMessage,
) -> dict[str, Any]:
    return {
        "role": _native_message_role(message.role),
        "content": str(redact_for_storage(message.content)),
        "created_at": message.created_at,
        "metadata": _redacted_mapping(message.metadata),
    }


def _native_import_session_metadata(ref: NativeSessionRef) -> dict[str, Any]:
    project_id = _optional_text(ref.metadata.get("project_id"))
    metadata: dict[str, Any] = {
        "source": "native_import",
        "source_harness_id": ref.harness_id,
        "native_ref_id": ref.id,
        "native_session_id": ref.native_session_id,
        "native_status": ref.status.value,
    }
    if project_id is not None:
        metadata["project_id"] = project_id
    if ref.workspace is not None:
        metadata["project_root"] = ref.workspace
    metadata.update(_native_snapshot_link_metadata(ref))
    return metadata


def _native_message_role(role: str) -> str:
    normalized = str(role).strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    if normalized == "model":
        return "assistant"
    return "assistant"


def _native_import_message_role(role: str) -> str | None:
    normalized = str(role).strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    if normalized == "model":
        return "assistant"
    return None


def _redacted_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = dict(value) if isinstance(value, Mapping) else {}
    redacted = redact_for_storage(value)
    return dict(redacted) if isinstance(redacted, Mapping) else {}
