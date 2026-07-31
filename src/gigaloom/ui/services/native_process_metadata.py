"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from fastapi import HTTPException

from gigaloom import proxy
from gigaloom.attachments import (
    AttachmentNotFoundError,
    FilesystemAttachmentStore,
    HarnessAttachment,
    attachment_to_dict,
)
from gigaloom.native.base import (
    NativeCommandPlan,
    NativePromptDelivery,
    NativePromptDeliveryStatus,
    native_prompt_delivery_to_dict,
)
from gigaloom.native.models import (
    HarnessInvocationMode,
    NativeExecutionSnapshot,
    NativeSessionRef,
    NativeSessionStatus,
    create_execution_snapshot,
    execution_snapshot_from_dict,
    execution_snapshot_to_dict,
)
from gigaloom.native.process import NativeProcessRef
from gigaloom.project import resolve_project
from gigaloom.provider_account_sessions import PROVIDER_ACCOUNT_BINDING_KEY
from gigaloom.sessions import HarnessSessionStore
from gigaloom.sessions.models import HarnessNativeLink, HarnessSession
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.types import parse_api_mode
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.workspace import resolve_workspace


def _native_process_run_metadata(
    options: Mapping[str, Any],
    process_ref: NativeProcessRef | None = None,
    *,
    prompt_delivery_status: NativePromptDeliveryStatus | None = None,
    prompt_delivery_error: str | None = None,
) -> dict[str, Any]:
    ref = options.get("native_ref")
    plan = options.get("plan")
    native_session_id = _optional_text(options.get("native_session_id"))
    metadata = {
        "invocation_mode": HarnessInvocationMode.NATIVE.value,
        "native_action": options["action"],
    }
    if native_session_id is not None:
        metadata["native_session_id"] = native_session_id
    if isinstance(ref, NativeSessionRef):
        metadata.update(
            {
                "native_ref_id": ref.id,
                "native_session_id": ref.native_session_id,
                "native_status": ref.status.value,
            }
        )
    elif isinstance(plan, NativeCommandPlan) and plan.native_home is not None:
        metadata["native_home"] = plan.native_home
    if isinstance(plan, NativeCommandPlan) and plan.execution_snapshot is not None:
        metadata["execution_snapshot"] = execution_snapshot_to_dict(
            plan.execution_snapshot
        )
    if isinstance(plan, NativeCommandPlan):
        telemetry = plan.metadata.get("telemetry")
        if isinstance(telemetry, Mapping):
            metadata["telemetry"] = dict(telemetry)
    if isinstance(plan, NativeCommandPlan) and plan.prompt_delivery is not None:
        process_delivery = (
            process_ref.metadata.get("prompt_delivery")
            if process_ref is not None
            else None
        )
        if isinstance(process_delivery, Mapping) and prompt_delivery_status is None:
            metadata["prompt_delivery"] = dict(process_delivery)
        else:
            metadata["prompt_delivery"] = native_prompt_delivery_to_dict(
                plan.prompt_delivery,
                status=prompt_delivery_status,
                error=prompt_delivery_error,
            )
    if process_ref is not None:
        metadata["native_process"] = {
            "id": process_ref.id,
            "pid": process_ref.pid,
            "transport": process_ref.transport,
            "status": process_ref.status.value,
        }
    attachments = options.get("attachments")
    if isinstance(attachments, tuple | list) and attachments:
        metadata["attachment_ids"] = list(options.get("attachment_ids") or ())
        metadata["attachments"] = [dict(attachment) for attachment in attachments]
    attachment_render_plan = options.get("attachment_render_plan")
    if isinstance(attachment_render_plan, Mapping):
        metadata["attachment_render_plan"] = dict(attachment_render_plan)
    preflight = options.get("preflight")
    if isinstance(preflight, Mapping):
        metadata["preflight"] = dict(preflight)
    route_preflight = options.get("proxy_route_preflight")
    if isinstance(route_preflight, proxy.ProxyRoutePreflight):
        metadata["proxy_preflight"] = proxy.proxy_route_preflight_to_dict(
            route_preflight
        )
    workspace_execution = options.get("workspace_execution")
    if isinstance(workspace_execution, Mapping):
        metadata["workspace_execution"] = dict(workspace_execution)
    policy = options.get("policy")
    if isinstance(policy, Mapping):
        metadata["policy"] = dict(policy)
    provider_account_binding = options.get(PROVIDER_ACCOUNT_BINDING_KEY)
    if isinstance(provider_account_binding, Mapping):
        metadata[PROVIDER_ACCOUNT_BINDING_KEY] = dict(provider_account_binding)
    return metadata


def _append_native_process_link(
    *,
    store: HarnessSessionStore,
    session: HarnessSession,
    options: Mapping[str, Any],
    process_ref: NativeProcessRef,
) -> HarnessNativeLink:
    ref = options.get("native_ref")
    native_session_id = _optional_text(options.get("native_session_id"))
    can_resume = native_session_id is not None
    resume_reason = None if can_resume else _native_missing_session_id_reason()
    status = (
        ref.status
        if isinstance(ref, NativeSessionRef)
        else NativeSessionStatus.MANAGED_NATIVE
    )
    now = utc_now()
    metadata: dict[str, Any] = {
        "native_action": options["action"],
        "native_process_id": process_ref.id,
        "run_id": process_ref.run_id,
        "can_resume": can_resume,
        "resume_reason": resume_reason,
        "command": list(process_ref.display_command),
        "process_status": process_ref.status.value,
    }
    workspace_execution = options.get("workspace_execution")
    if isinstance(workspace_execution, Mapping):
        metadata["workspace_execution"] = dict(workspace_execution)
    policy = options.get("policy")
    if isinstance(policy, Mapping):
        metadata["policy"] = dict(policy)
    provider_account_binding = options.get(PROVIDER_ACCOUNT_BINDING_KEY)
    if isinstance(provider_account_binding, Mapping):
        metadata[PROVIDER_ACCOUNT_BINDING_KEY] = dict(provider_account_binding)
    if process_ref.native_home is not None:
        metadata["native_home"] = process_ref.native_home
    if isinstance(ref, NativeSessionRef):
        metadata.update(
            {
                "source_ref_status": ref.status.value,
                "source_ref_can_resume": ref.can_resume,
                "source_ref_resume_reason": ref.resume_reason,
            }
        )
    if isinstance(options.get("plan"), NativeCommandPlan):
        metadata["plan_metadata"] = dict(options["plan"].metadata)
        if options["plan"].prompt_delivery is not None:
            process_delivery = process_ref.metadata.get("prompt_delivery")
            metadata["prompt_delivery"] = (
                dict(process_delivery)
                if isinstance(process_delivery, Mapping)
                else native_prompt_delivery_to_dict(options["plan"].prompt_delivery)
            )
        if options["plan"].execution_snapshot is not None:
            snapshot = options["plan"].execution_snapshot
            metadata["execution_snapshot"] = execution_snapshot_to_dict(snapshot)
            metadata["limitations"] = [] if snapshot.route_known else ["route_unknown"]
    return store.append_native_link(
        session.id,
        HarnessNativeLink(
            id=new_id("nlink"),
            session_id=session.id,
            harness_id=str(options["harness_id"]),
            status=status,
            created_at=now,
            updated_at=now,
            native_session_id=native_session_id,
            native_ref_id=ref.id if isinstance(ref, NativeSessionRef) else None,
            source=f"native_process_{options['action']}",
            workspace=_optional_text(options.get("source_workspace"))
            or session.workspace,
            metadata=metadata,
        ),
    )


def _native_ref_from_session_link(
    store: HarnessSessionStore, session: HarnessSession, harness_id: str
) -> NativeSessionRef:
    link = store.get_native_link(session.id, harness_id)
    if link is None:
        raise HTTPException(
            status_code=400,
            detail="native_ref_id is required or session native link is unavailable",
        )
    can_resume = bool(link.metadata.get("can_resume")) and bool(link.native_session_id)
    resume_reason = _optional_text(link.metadata.get("resume_reason"))
    if not can_resume and resume_reason is None:
        resume_reason = _native_missing_session_id_reason()
    return NativeSessionRef(
        id=link.native_ref_id or link.id,
        harness_id=link.harness_id,
        native_session_id=link.native_session_id,
        title=str(link.metadata.get("title") or "Managed native session"),
        workspace=link.workspace or session.workspace,
        source=link.source or "native_process",
        status=link.status,
        created_at=link.created_at,
        updated_at=link.updated_at,
        message_count=None,
        can_preview=False,
        can_import=False,
        can_resume=can_resume,
        resume_reason=resume_reason,
        metadata=link.metadata,
        execution_snapshot=execution_snapshot_from_dict(
            _metadata_mapping(link.metadata.get("execution_snapshot"))
        ),
    )


def _native_ref_with_reviewed_resume_snapshot(
    *,
    ref: NativeSessionRef,
    payload: Mapping[str, Any],
    session: HarnessSession,
    data_dir: str,
) -> NativeSessionRef:
    if ref.execution_snapshot is not None:
        return ref
    if ref.harness_id not in {"codex-cli", "claude-code", "gemini-cli"}:
        return ref
    explicit_api_mode = _optional_text(payload.get("api_mode"))
    if explicit_api_mode is None:
        raise HTTPException(
            status_code=400,
            detail="route_unknown: legacy native ref requires an explicit reviewed api_mode before resume",
        )
    api_mode = parse_api_mode(explicit_api_mode)
    workspace = resolve_workspace(
        _optional_text(payload.get("workspace")) or ref.workspace or session.workspace
    )
    project_id = _optional_text(ref.metadata.get("project_id"))
    if project_id is None:
        if workspace is None:
            raise HTTPException(
                status_code=400, detail="Legacy native ref is missing project identity"
            )
        project_id = resolve_project(workspace, data_dir=data_dir).id
    native_home = _optional_text(ref.metadata.get("native_home"))
    if native_home is None:
        family = {
            "codex-cli": "codex",
            "claude-code": "claude",
            "gemini-cli": "gemini",
        }[ref.harness_id]
        native_home = str(
            Path(data_dir).expanduser() / "native" / family / "homes" / project_id
        )
    snapshot = create_execution_snapshot(
        harness_id=ref.harness_id,
        api_mode=api_mode.value,
        model=_optional_text(payload.get("model"))
        or _optional_text(ref.metadata.get("model"))
        or session.default_model,
        native_home=native_home,
        workspace=workspace,
        project_id=project_id,
        permission_mode=str(payload.get("mode") or session.default_mode),
        tool_config_hash=_optional_text(ref.metadata.get("tool_config_hash")),
        route_known=False,
        warnings=(
            "Legacy native ref had no route snapshot; this explicit route override applies only to the reviewed resume.",
        ),
    )
    return replace(ref, execution_snapshot=snapshot)


def _reject_resume_snapshot_overrides(
    payload: Mapping[str, Any], snapshot: NativeExecutionSnapshot | None
) -> None:
    if snapshot is None:
        return
    checks = {
        "api_mode": snapshot.api_mode,
        "model": snapshot.model,
        "mode": snapshot.permission_mode,
        "workspace": snapshot.workspace,
    }
    for key, expected in checks.items():
        if key not in payload or payload.get(key) is None:
            continue
        actual = str(payload[key]).strip()
        if key == "api_mode":
            actual = parse_api_mode(actual).value
        elif key == "workspace":
            actual = resolve_workspace(actual) or ""
        if actual != (expected or ""):
            raise HTTPException(
                status_code=400,
                detail=f"Native resume {key} contradicts the execution snapshot",
            )


def _native_snapshot_link_metadata(ref: NativeSessionRef) -> dict[str, Any]:
    if ref.execution_snapshot is None:
        return {"limitations": ["route_unknown"]} if ref.can_resume else {}
    return {
        "execution_snapshot": execution_snapshot_to_dict(ref.execution_snapshot),
        "limitations": [] if ref.execution_snapshot.route_known else ["route_unknown"],
    }


def _native_session_id_from_plan(plan: NativeCommandPlan) -> str | None:
    metadata = dict(plan.metadata)
    for key in (
        "native_session_id",
        "managed_session_id",
        "session_name",
        "session_id",
        "codex_session_id",
        "claude_session_id",
        "gemini_session_id",
    ):
        value = _optional_text(metadata.get(key))
        if value is not None:
            return value
    return None


def _native_prompt_idempotency_key(session_id: str, client_key: str) -> str:
    digest = hashlib.sha256(f"{session_id}\x00{client_key}".encode("utf-8")).hexdigest()
    return f"nprompt_{digest[:32]}"


def _reject_duplicate_native_prompt_delivery(
    store: HarnessSessionStore, session_id: str, delivery: NativePromptDelivery | None
) -> None:
    if delivery is None:
        return
    for existing_run in store.list_runs(session_id):
        existing = existing_run.metadata.get("prompt_delivery")
        if not isinstance(existing, Mapping):
            continue
        if existing.get("idempotency_key") != delivery.idempotency_key:
            continue
        if existing.get("prompt_sha256") != delivery.prompt_sha256:
            raise ValueError(
                "Native prompt idempotency key is already bound to a different prompt"
            )
        state = str(existing.get("status") or "pending")
        raise ValueError(f"Native prompt delivery was already recorded as {state}")


def _native_missing_session_id_reason() -> str:
    return "Native session id was not detected yet; sync native sessions after the CLI writes history."


def _attachment_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("attachment_ids must be a list")
    ids: list[str] = []
    for item in value:
        attachment_id = _optional_text(item)
        if attachment_id is None:
            raise ValueError("attachment_ids must contain non-empty strings")
        ids.append(attachment_id)
    return tuple(ids)


def _load_native_attachments(
    attachment_store: FilesystemAttachmentStore,
    session_id: str,
    attachment_ids: tuple[str, ...],
) -> tuple[HarnessAttachment, ...]:
    attachments: list[HarnessAttachment] = []
    for attachment_id in attachment_ids:
        try:
            attachment = attachment_store.get_attachment(attachment_id)
        except AttachmentNotFoundError as exc:
            raise ValueError(f"Unknown attachment id: {attachment_id}") from exc
        if attachment.session_id != session_id:
            raise ValueError(f"Attachment does not belong to session: {attachment_id}")
        attachments.append(attachment)
    return tuple(attachments)


def _native_attachment_metadata(attachment: HarnessAttachment) -> dict[str, Any]:
    payload = attachment_to_dict(attachment)
    payload.pop("storage_path", None)
    return payload


def _native_request_extra(
    extra: Mapping[str, Any],
    attachments: tuple[Mapping[str, Any], ...],
    attachment_render_plan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(extra)
    if attachments:
        payload["attachment_ids"] = [
            str(attachment["id"]) for attachment in attachments
        ]
        payload["attachments"] = [dict(attachment) for attachment in attachments]
    if attachment_render_plan:
        payload["attachment_render_plan"] = dict(attachment_render_plan)
    return payload
