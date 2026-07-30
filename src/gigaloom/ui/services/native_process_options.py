"""Native UI application helpers extracted from the composition root."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from fastapi import HTTPException

from gigaloom import proxy
from gigaloom.attachments import (
    FilesystemAttachmentStore,
    render_attachments_for_harness,
    render_plan_to_dict,
)
from gigaloom.cli_capabilities import CliCapabilitySnapshot
from gigaloom.config import HarnessConfig
from gigaloom.harnesses.attachment_plan import attachment_capability_error
from gigaloom.native.base import NativeCommandPlan
from gigaloom.native.models import HarnessInvocationMode
from gigaloom.native.process import NativeProcessStartError
from gigaloom.native.registry import NativeHistoryConnectorRegistry
from gigaloom.native.store import NativeSessionIndexStore
from gigaloom.preflight import (
    PreflightBlockedError,
    build_preflight_report,
    preflight_report_to_dict,
)
from gigaloom.registry import HarnessRegistry
from gigaloom.sessions import HarnessSessionStore
from gigaloom.sessions.models import HarnessSession
from gigaloom.sessions.store import new_id
from gigaloom.types import (
    GigaChatApiMode,
    HarnessCapability,
    HarnessRequest,
    parse_api_mode,
    parse_capability,
)
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.native_process_metadata import (
    _attachment_ids,
    _load_native_attachments,
    _native_attachment_metadata,
    _native_prompt_idempotency_key,
    _native_ref_from_session_link,
    _native_ref_with_reviewed_resume_snapshot,
    _native_request_extra,
    _native_session_id_from_plan,
    _reject_duplicate_native_prompt_delivery,
    _reject_resume_snapshot_overrides,
)
from gigaloom.ui.services.native_process_policy import _native_permission_mode
from gigaloom.ui.services.native_sessions import (
    _native_connector_or_404,
    _native_ref_or_404,
)
from gigaloom.ui.services.request_values import (
    optional_text as _optional_text,
)
from gigaloom.ui.services.request_values import (
    required_text as _required_text,
)
from gigaloom.workspace import resolve_workspace


def _require_native_cli_compatibility(
    registry: HarnessRegistry, harness_id: str
) -> CliCapabilitySnapshot | None:
    """Reject native starts when a built-in CLI contract is not proven."""
    if harness_id not in registry.ids():
        return None
    capability_probe = getattr(registry.get(harness_id), "capability_probe", None)
    if not callable(capability_probe):
        return None
    snapshot = capability_probe()
    if snapshot.compatible:
        return snapshot
    raise NativeProcessStartError(
        snapshot.warning or f"{harness_id} is not adapter-compatible"
    )


def _plan_with_native_telemetry(
    plan: NativeCommandPlan,
    snapshot: CliCapabilitySnapshot,
    *,
    api_mode: GigaChatApiMode,
) -> NativeCommandPlan:
    """Bind truthful native observability evidence to the durable process plan."""
    return replace(
        plan,
        metadata={
            **dict(plan.metadata),
            "telemetry": {
                "api_mode": api_mode.value,
                "binary_version": snapshot.parsed_version or snapshot.version,
                "event_schema": snapshot.native_event_schema,
                "structured_events": snapshot.native_structured_events,
                "transport": "structured"
                if snapshot.native_structured_events
                else "raw_terminal",
                "observability_limits": []
                if snapshot.native_structured_events
                else [
                    "tool_lifecycle_opaque",
                    "usage_unavailable",
                    "artifacts_unclassified",
                ],
            },
        },
    )


def _native_process_start_options(
    *,
    payload: Mapping[str, Any],
    session: HarnessSession,
    config: HarnessConfig,
    registry: HarnessRegistry,
    native_registry: NativeHistoryConnectorRegistry,
    native_index_store: NativeSessionIndexStore,
    store: HarnessSessionStore,
    attachment_store: FilesystemAttachmentStore,
) -> dict[str, Any]:
    action = str(payload.get("action") or "start").strip().lower()
    if action not in {"start", "resume"}:
        raise ValueError("action must be start or resume")
    if action == "resume":
        return _native_process_resume_options(
            payload=payload,
            session=session,
            config=config,
            registry=registry,
            native_registry=native_registry,
            native_index_store=native_index_store,
            store=store,
        )
    return _native_process_new_options(
        payload=payload,
        session=session,
        config=config,
        registry=registry,
        native_registry=native_registry,
        attachment_store=attachment_store,
        store=store,
    )


def _native_process_new_options(
    *,
    payload: Mapping[str, Any],
    session: HarnessSession,
    config: HarnessConfig,
    registry: HarnessRegistry,
    native_registry: NativeHistoryConnectorRegistry,
    attachment_store: FilesystemAttachmentStore,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    harness_id = _required_text(
        payload.get("harness_id") or session.default_harness_id,
        "harness_id is required",
    )
    connector = _native_connector_or_404(native_registry, harness_id)
    cli_capabilities = _require_native_cli_compatibility(registry, harness_id)
    api_mode = parse_api_mode(payload.get("api_mode") or session.default_api_mode)
    capability = parse_capability(
        payload.get("capability") or HarnessCapability.AGENT_CLI.value
    )
    workspace = resolve_workspace(
        _optional_text(payload.get("workspace")) or session.workspace
    )
    prompt = str(payload.get("prompt") or "")
    model = _optional_text(payload.get("model")) or session.default_model
    mode = _native_permission_mode(payload.get("mode") or session.default_mode)
    attachment_ids = _attachment_ids(payload.get("attachment_ids"))
    attachments = _load_native_attachments(attachment_store, session.id, attachment_ids)
    preflight = build_preflight_report(
        prompt=prompt,
        workspace=workspace,
        attachments=attachments,
        data_dir=config.data_dir,
    )
    if preflight.hard_block:
        raise PreflightBlockedError(preflight)
    preflight_payload = preflight_report_to_dict(preflight)
    attachment_payloads = tuple(
        (_native_attachment_metadata(attachment) for attachment in attachments)
    )
    attachment_render_plan = (
        render_attachments_for_harness(
            harness_id, attachments, attachment_store, prompt=prompt
        )
        if attachments
        else None
    )
    attachment_render_plan_payload = (
        render_plan_to_dict(attachment_render_plan)
        if attachment_render_plan is not None
        else None
    )
    extra = _native_request_extra(
        _metadata_mapping(payload.get("extra")),
        attachment_payloads,
        attachment_render_plan_payload,
    )
    extra["preflight"] = preflight_payload
    extra["native_prompt_idempotency_key"] = _native_prompt_idempotency_key(
        session.id,
        _optional_text(payload.get("idempotency_key"))
        or new_id("native_prompt_submit"),
    )
    request = HarnessRequest(
        prompt=prompt,
        model=model,
        api_mode=api_mode,
        capability=capability,
        mode=mode,
        invocation_mode=HarnessInvocationMode.NATIVE,
        workspace=workspace,
        session_id=session.id,
        attachments=attachment_payloads,
        attachment_render_plan=attachment_render_plan_payload,
        extra=extra,
    )
    if cli_capabilities is not None:
        attachment_error = attachment_capability_error(
            request, cli_capabilities.capabilities, surface="native"
        )
        if attachment_error is not None:
            raise NativeProcessStartError(attachment_error)
    context = config.to_context()
    route_preflight = None
    if bool(getattr(connector, "requires_proxy_preflight", False)):
        route_preflight = proxy.ensure_proxy_route_available(context, api_mode)
        if not route_preflight.ok:
            raise NativeProcessStartError(
                route_preflight.error or "Native proxy preflight failed"
            )
        context = replace(
            context,
            api_key=route_preflight.api_key or context.api_key,
            harness_model_key=route_preflight.harness_model_key
            or context.harness_model_key,
        )
    try:
        plan = connector.build_start_command(request, context)
        if cli_capabilities is not None:
            plan = _plan_with_native_telemetry(
                plan, cli_capabilities, api_mode=api_mode
            )
        if route_preflight is not None:
            plan = replace(
                plan,
                metadata={
                    **dict(plan.metadata),
                    "proxy_preflight": proxy.proxy_route_preflight_to_dict(
                        route_preflight
                    ),
                },
            )
        _reject_duplicate_native_prompt_delivery(
            store, session.id, plan.prompt_delivery
        )
    except (OSError, ValueError):
        if route_preflight is not None:
            proxy.stop_owned_sidecar(route_preflight.startup)
        raise
    native_session_id = _native_session_id_from_plan(plan)
    return {
        "action": "start",
        "plan": plan,
        "harness_id": harness_id,
        "prompt": prompt,
        "model": model,
        "api_mode": api_mode,
        "capability": capability,
        "mode": mode,
        "workspace": workspace,
        "native_ref": None,
        "native_session_id": native_session_id,
        "connector": connector,
        "attachment_ids": attachment_ids,
        "attachments": attachment_payloads,
        "attachment_render_plan": attachment_render_plan_payload,
        "preflight": preflight_payload,
        "proxy_route_preflight": route_preflight,
    }


def _native_process_resume_options(
    *,
    payload: Mapping[str, Any],
    session: HarnessSession,
    config: HarnessConfig,
    registry: HarnessRegistry,
    native_registry: NativeHistoryConnectorRegistry,
    native_index_store: NativeSessionIndexStore,
    store: HarnessSessionStore,
) -> dict[str, Any]:
    native_ref_id = _optional_text(payload.get("native_ref_id"))
    if native_ref_id is not None:
        ref = _native_ref_or_404(native_index_store, native_ref_id)
    else:
        harness_id = _required_text(
            payload.get("harness_id") or session.default_harness_id,
            "harness_id is required",
        )
        ref = _native_ref_from_session_link(store, session, harness_id)
    if not ref.can_resume:
        raise HTTPException(
            status_code=400,
            detail=ref.resume_reason or "Native session cannot be resumed",
        )
    connector = _native_connector_or_404(native_registry, ref.harness_id)
    cli_capabilities = _require_native_cli_compatibility(registry, ref.harness_id)
    ref = _native_ref_with_reviewed_resume_snapshot(
        ref=ref, payload=payload, session=session, data_dir=config.data_dir
    )
    snapshot = ref.execution_snapshot
    _reject_resume_snapshot_overrides(payload, snapshot)
    api_mode = parse_api_mode(
        snapshot.api_mode
        if snapshot is not None
        else payload.get("api_mode") or session.default_api_mode
    )
    capability = parse_capability(
        payload.get("capability") or HarnessCapability.AGENT_CLI.value
    )
    workspace = resolve_workspace(
        snapshot.effective_workspace or snapshot.workspace
        if snapshot is not None
        else _optional_text(payload.get("workspace"))
        or ref.workspace
        or session.workspace
    )
    context = config.to_context()
    route_preflight = None
    if bool(getattr(connector, "requires_proxy_preflight", False)):
        route_preflight = proxy.ensure_proxy_route_available(context, api_mode)
        if not route_preflight.ok:
            raise NativeProcessStartError(
                route_preflight.error or "Native proxy preflight failed"
            )
        context = replace(
            context,
            api_key=route_preflight.api_key or context.api_key,
            harness_model_key=route_preflight.harness_model_key
            or context.harness_model_key,
        )
    try:
        plan = connector.build_resume_command(ref, context)
        if cli_capabilities is not None:
            plan = _plan_with_native_telemetry(
                plan, cli_capabilities, api_mode=api_mode
            )
    except (OSError, ValueError):
        if route_preflight is not None:
            proxy.stop_owned_sidecar(route_preflight.startup)
        raise
    if route_preflight is not None:
        plan = replace(
            plan,
            metadata={
                **dict(plan.metadata),
                "proxy_preflight": proxy.proxy_route_preflight_to_dict(route_preflight),
            },
        )
    prompt = (
        _optional_text(payload.get("prompt")) or f"Resume native session: {ref.title}"
    )
    return {
        "action": "resume",
        "plan": plan,
        "harness_id": ref.harness_id,
        "prompt": prompt,
        "model": snapshot.model
        if snapshot is not None
        else _optional_text(payload.get("model"))
        or _optional_text(ref.metadata.get("model"))
        or session.default_model,
        "api_mode": api_mode,
        "capability": capability,
        "mode": snapshot.permission_mode
        if snapshot is not None
        else str(payload.get("mode") or session.default_mode),
        "workspace": workspace,
        "native_ref": ref,
        "native_session_id": ref.native_session_id,
        "attachment_ids": (),
        "attachments": (),
        "attachment_render_plan": None,
        "connector": connector,
        "proxy_route_preflight": route_preflight,
    }
