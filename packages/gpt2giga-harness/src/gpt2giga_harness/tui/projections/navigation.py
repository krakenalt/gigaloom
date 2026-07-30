"""Typed navigation projections for TUI clients."""

from __future__ import annotations

from typing import Any, Mapping


from gpt2giga_harness.integration_flows import IntegrationFlowService
from gpt2giga_harness.project import (
    HarnessProject,
)
from gpt2giga_harness.diagnostics.inventory.capabilities import (
    legacy_mode_compatibility_receipt,
)
from gpt2giga_harness.sessions.api import SessionQueryStore
from gpt2giga_harness.sessions.models import (
    HarnessMessage,
    HarnessSession,
)

from gpt2giga_harness.tui.contracts import (
    MAX_DISPLAY_CHARS,
    ProjectSummary,
    SessionSummary,
    SessionActionBinding,
    SessionPreview,
    HarnessSummary,
    ReadinessSummary,
    IntegrationSummary,
)

from gpt2giga_harness.tui.projections.values import (
    _bounded_non_negative_int,
    _bounded_content_text,
    _mapping,
    _mapping_items,
    _required_text,
    _optional_text,
    _display_text,
    _optional_display_text,
    _required_identity,
    _hash_generation,
    _neutralize_presentation_text,
    _optional_identity,
)
from gpt2giga_harness.tui.clients.session_queries import session_navigation_records


def _in_process_integration_summary(
    service: IntegrationFlowService,
) -> IntegrationSummary:
    try:
        catalog_count = len(service.catalog.list())
        flows = service.list()
    except (OSError, RuntimeError, ValueError):
        return IntegrationSummary("blocked", 0, 0, 0)
    verified = sum(
        getattr(item.status, "value", item.status) in {"verified", "active"}
        for item in flows
    )
    return IntegrationSummary("ready", catalog_count, len(flows), verified)


def _integration_summary_from_mapping(data: Mapping[str, Any]) -> IntegrationSummary:
    catalog = _mapping_items(data.get("catalog"), 200)
    flows = _mapping_items(data.get("flows"), 200)
    verified = sum(
        str(item.get("status") or "") in {"verified", "active"} for item in flows
    )
    return IntegrationSummary("ready", len(catalog), len(flows), verified)


def _selected_session_id(
    sessions: tuple[HarnessSession, ...],
    requested: str | None,
) -> str | None:
    if requested and any(item.id == requested for item in sessions):
        return requested
    return sessions[0].id if sessions else None


def _selected_summary_id(
    sessions: tuple[SessionSummary, ...],
    requested: str | None,
) -> str | None:
    if requested and any(item.id == requested for item in sessions):
        return requested
    return sessions[0].id if sessions else None


def _project_summary(project: HarnessProject, *, session_count: int) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=_display_text(project.name),
        root=project.root,
        git_branch=_optional_display_text(project.git_branch),
        session_count=session_count,
    )


def _project_summary_from_mapping(
    data: Mapping[str, Any],
    *,
    session_count: int,
) -> ProjectSummary:
    return ProjectSummary(
        id=_required_text(data.get("id"), "project id"),
        name=_display_text(data.get("name") or "Project"),
        root=_required_text(data.get("root"), "project root"),
        git_branch=_optional_display_text(data.get("git_branch")),
        session_count=session_count,
    )


def _session_summary(
    session: HarnessSession,
    store: SessionQueryStore | None = None,
) -> SessionSummary:
    native = _session_native_reference(session.native, session.metadata)
    task_intent, authority, warning = _workbench_selection_summary(
        session.metadata,
        mode=session.default_mode,
    )
    preview_message, lease = (
        session_navigation_records(store, session.id)
        if store is not None
        else (None, None)
    )
    return SessionSummary(
        id=session.id,
        title=_display_text(session.title),
        updated_at=session.updated_at,
        workspace=session.workspace,
        harness_id=session.default_harness_id,
        model=_optional_display_text(session.default_model),
        mode=session.default_mode,
        archived=session.archived,
        project_id=_optional_text(session.metadata.get("project_id")),
        preview=(
            _bounded_content_text(" ".join(preview_message.content.split()), 120)
            if preview_message is not None
            else ""
        ),
        native_authority=_optional_display_text(native.get("authority")),
        native_session_id=_optional_identity(
            native.get("native_id") or native.get("session_id") or native.get("id")
        ),
        native_operation=_optional_display_text(native.get("operation")),
        revision=session.updated_at,
        generation=_session_generation(native),
        lease=lease,
        task_intent=task_intent,
        authority=authority,
        compatibility_warning=warning,
    )


def _session_summary_from_mapping(data: Mapping[str, Any]) -> SessionSummary:
    metadata = _mapping(data.get("metadata"))
    native = _mapping(data.get("native_session_reference"))
    if not native:
        native = _session_native_reference(_mapping(data.get("native")), metadata)
    mode = _display_text(data.get("default_mode") or "plan")
    task_intent, authority, warning = _workbench_selection_summary(
        metadata,
        mode=mode,
    )
    return SessionSummary(
        id=_required_text(data.get("id"), "session id"),
        title=_display_text(data.get("title") or "Untitled session"),
        updated_at=_display_text(data.get("updated_at") or "unknown"),
        workspace=_optional_text(data.get("workspace")),
        harness_id=_display_text(data.get("default_harness_id") or "unknown"),
        model=_optional_display_text(data.get("default_model")),
        mode=mode,
        archived=bool(data.get("archived")),
        project_id=_optional_text(data.get("project_id")),
        preview=_bounded_content_text(data.get("last_message_preview"), 120),
        native_authority=_optional_display_text(native.get("authority")),
        native_session_id=_optional_identity(
            native.get("native_id") or native.get("session_id") or native.get("id")
        ),
        native_operation=_optional_display_text(native.get("operation")),
        revision=_required_text(
            data.get("session_revision") or data.get("updated_at"),
            "session revision",
        ),
        generation=_bounded_non_negative_int(
            data.get("session_generation")
            if data.get("session_generation") is not None
            else _session_generation(native)
        ),
        lease=_optional_identity(data.get("session_lease")),
        task_intent=task_intent,
        authority=authority,
        compatibility_warning=warning,
    )


def _workbench_selection_summary(
    metadata: Mapping[str, Any],
    *,
    mode: str,
) -> tuple[str, str, str | None]:
    retained = _mapping(metadata.get("workbench_selection"))
    if retained.get("schema_version") == 1:
        intent = str(retained.get("intent") or "")
        authority = str(retained.get("authority") or "")
        if intent in {"ask", "review", "change"} and authority in {
            "read_only",
            "workspace_write",
        }:
            return (
                intent,
                authority,
                _optional_display_text(retained.get("compatibility_warning")),
            )
    legacy = legacy_mode_compatibility_receipt(mode)
    return (
        str(legacy["intent"]),
        str(legacy["authority"]),
        _optional_display_text(legacy.get("warning")),
    )


def _session_native_reference(
    native: Mapping[str, Any], metadata: Mapping[str, Any]
) -> Mapping[str, Any]:
    explicit = _mapping(metadata.get("native_session_reference"))
    if explicit:
        return explicit
    structured = _mapping(metadata.get("structured_session_link"))
    if structured:
        return {
            "authority": structured.get("provider")
            or structured.get("authority")
            or native.get("harness_id"),
            "native_id": structured.get("thread_id")
            or structured.get("session_id")
            or structured.get("native_session_id"),
            "operation": structured.get("operation") or "resume",
            "revision": structured.get("revision"),
            "link_hash": structured.get("link_hash"),
        }
    return native


def _session_generation(native: Mapping[str, Any]) -> int:
    revision = native.get("revision")
    if isinstance(revision, int) and revision >= 0:
        return revision
    return _hash_generation(_optional_text(native.get("link_hash")))


def session_action_binding(
    session: SessionSummary, *, idempotency_key: str
) -> SessionActionBinding:
    """Bind one navigation action to the exact presented session state."""
    return SessionActionBinding(
        session_id=session.id,
        revision=session.revision,
        generation=session.generation,
        lease=session.lease,
        idempotency_key=_required_identity(idempotency_key, "idempotency key"),
    )


def _session_binding_payload(binding: SessionActionBinding) -> dict[str, Any]:
    return {
        "session_revision": binding.revision,
        "session_generation": binding.generation,
        "session_lease": binding.lease,
        "idempotency_key": binding.idempotency_key,
    }


def _session_preview_from_mapping(data: Mapping[str, Any]) -> SessionPreview:
    return SessionPreview(
        session=_session_summary_from_mapping(_mapping(data.get("session"))),
        transcript=tuple(
            _bounded_content_text(item, MAX_DISPLAY_CHARS)
            for item in data.get("transcript", ())
            if isinstance(item, str)
        )[:100],
        match_count=_bounded_non_negative_int(data.get("match_count")),
        truncated=bool(data.get("truncated")),
    )


def _message_preview(message: HarnessMessage) -> str:
    content = _bounded_content_text(message.content, MAX_DISPLAY_CHARS)
    return f"{_display_text(message.role).upper()} · {message.created_at}\n{content}"


def _session_export_text(
    session: HarnessSession, messages: tuple[HarnessMessage, ...]
) -> str:
    header = (
        f"# {_display_text(session.title)}\n\n"
        f"Session: `{session.id}`  \n"
        f"Workspace: conversation context only; filesystem restore is not included.\n\n"
    )
    transcript = "\n\n".join(
        f"## {_display_text(item.role).title()} · {item.created_at}\n\n"
        f"{_neutralize_presentation_text(item.content)}"
        for item in messages
    )
    return f"{header}{transcript}\n"


def _harness_summary(
    spec: Mapping[str, Any],
    availability: Mapping[str, Any],
    transport: Mapping[str, Any],
) -> HarnessSummary:
    return HarnessSummary(
        id=_required_text(spec.get("id"), "Harness id"),
        title=_display_text(spec.get("title") or spec.get("id") or "Harness"),
        availability=_display_text(availability.get("status") or "unknown"),
        reason=_display_text(availability.get("reason") or "not checked"),
        default_transport=_display_text(transport.get("default") or "one_shot"),
    )


def _readiness_summary(
    readiness: Mapping[str, Any],
    *,
    session: Mapping[str, Any] | None,
    harnesses: tuple[HarnessSummary, ...],
    harness_id: str,
    model: str | None,
) -> ReadinessSummary:
    selected_harness = next(
        (item for item in harnesses if item.id == harness_id),
        None,
    )
    provider, provider_status = _provider_binding(session)
    plan = _mapping(readiness.get("plan"))
    findings = tuple(
        _display_text(item.get("id") or item.get("status") or "finding")
        for item in _mapping_items(readiness.get("findings"), 20)
        if str(item.get("status") or "") in {"blocked", "degraded", "unknown"}
    )
    return ReadinessSummary(
        status=_display_text(readiness.get("status") or "unknown"),
        provider=provider,
        provider_status=provider_status,
        harness_id=harness_id,
        harness_status=(
            selected_harness.availability if selected_harness else "unknown"
        ),
        model=model,
        transport=_display_text(
            plan.get("execution_transport")
            or (selected_harness.default_transport if selected_harness else "unknown")
        ),
        findings=findings,
    )


def _provider_binding(session: Mapping[str, Any] | None) -> tuple[str, str]:
    if not session:
        return "pending execution snapshot", "not_checked"
    metadata = _mapping(session.get("metadata"))
    snapshot = _mapping(metadata.get("execution_snapshot"))
    provider = _mapping(snapshot.get("provider"))
    provider_id = _optional_display_text(provider.get("id"))
    revision = _optional_display_text(provider.get("revision"))
    if provider_id:
        return (
            f"{provider_id}@{revision}" if revision else provider_id,
            "bound",
        )
    return "pending execution snapshot", "not_checked"
