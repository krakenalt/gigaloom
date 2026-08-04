"""Domain router extracted from the FastAPI composition root."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from typing import Any, Mapping

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from gigaloom import proxy
from gigaloom.native.base import NativePromptDeliveryStatus
from gigaloom.native.models import HarnessInvocationMode, NativeSessionRef
from gigaloom.native.process import (
    NativeProcessStartError,
    native_process_ref_to_dict,
)
from gigaloom.provenance import run_provenance_to_dict
from gigaloom.provider_account_sessions import (
    PROVIDER_ACCOUNT_BINDING_KEY,
    ProviderAccountSessionError,
    prepare_provider_account_binding,
)
from gigaloom.provider_authentication_broker import NativeLoginBroker
from gigaloom.sessions import SessionNotFoundError
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessStoredEvent,
    native_link_to_dict,
    run_to_dict,
)
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.container import AppServices
from gigaloom.ui.services.attachments import (
    metadata_mapping as _metadata_mapping,
)
from gigaloom.ui.services.native_process_errors import _native_timeout_seconds
from gigaloom.ui.services.native_process_metadata import (
    _append_native_process_link,
    _native_process_run_metadata,
    _native_ref_from_session_link,
)
from gigaloom.ui.services.native_process_options import (
    _native_process_start_options,
)
from gigaloom.ui.services.native_process_policy import (
    _native_permission_mode,
    _native_process_policy_gate,
    _native_resume_workspace_execution,
)
from gigaloom.ui.services.native_sessions import _native_ref_or_404
from gigaloom.ui.services.provenance import _build_current_run_provenance
from gigaloom.ui.services.request_values import (
    optional_text as _optional_text,
)
from gigaloom.ui.services.request_values import (
    required_text as _required_text,
)
from gigaloom.workspace import resolve_workspace
from gigaloom.worktrees import (
    WorktreeError,
    discard_run_worktree,
    prepare_workspace_execution,
)


def create_router(
    services: AppServices, *, native_login_broker: NativeLoginBroker | None
) -> APIRouter:
    """Create the native domain router with typed application services."""
    router = ContractAPIRouter()

    @router.proc.post("/api/native/processes/start", response_model=None)
    def native_process_start(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any] | JSONResponse:
        run = None
        process_ref = None
        options = None
        workspace_execution = None
        try:
            session = services.session_store.get_session(
                _required_text(payload.get("session_id"), "session_id is required")
            )
            action = str(payload.get("action") or "start").strip().lower()
            if action not in {"start", "resume"}:
                raise ValueError("action must be start or resume")
            if action == "resume":
                identity_native_ref_id = _optional_text(payload.get("native_ref_id"))
                if identity_native_ref_id is not None:
                    identity_ref = _native_ref_or_404(
                        services.native_index_store, identity_native_ref_id
                    )
                else:
                    identity_harness_id = _required_text(
                        payload.get("harness_id") or session.default_harness_id,
                        "harness_id is required",
                    )
                    identity_ref = _native_ref_from_session_link(
                        services.session_store, session, identity_harness_id
                    )
                identity_provider_id = identity_ref.harness_id
                identity_native_session_id = (
                    identity_ref.native_session_id or identity_ref.id
                )
            else:
                identity_provider_id = _required_text(
                    payload.get("harness_id") or session.default_harness_id,
                    "harness_id is required",
                )
                identity_native_session_id = None
            provider_account_binding = prepare_provider_account_binding(
                session,
                provider_id=identity_provider_id,
                native_session_id=identity_native_session_id,
                provider=native_login_broker,
            )
            if provider_account_binding is not None and (
                not session.metadata.get(PROVIDER_ACCOUNT_BINDING_KEY)
            ):
                session = services.session_store.update_session(
                    session.id,
                    metadata={
                        **dict(session.metadata),
                        PROVIDER_ACCOUNT_BINDING_KEY: provider_account_binding,
                    },
                )
            if action == "start":
                _native_permission_mode(payload.get("mode") or session.default_mode)
            policy_result = _native_process_policy_gate(
                payload=payload,
                session=session,
                policy_engine=services.policy_engine,
                runtime_store=services.runtime_store,
            )
            if isinstance(policy_result, JSONResponse):
                return policy_result
            run_id, policy_metadata = policy_result
            effective_payload = dict(payload)
            if action == "start":
                mode = _native_permission_mode(
                    payload.get("mode") or session.default_mode
                )
                source_workspace = resolve_workspace(
                    _optional_text(payload.get("workspace")) or session.workspace
                )
                if mode == "edit" and source_workspace is None:
                    raise WorktreeError(
                        "Native edit isolation requires an explicit workspace; refusing to inherit the UI server checkout."
                    )
                workspace_execution = prepare_workspace_execution(
                    requested_policy=payload.get("workspace_policy"),
                    harness_kind="agent-cli",
                    mode=mode,
                    workspace=source_workspace,
                    data_dir=services.config.data_dir,
                    session_id=session.id,
                    run_id=run_id,
                )
                effective_payload["mode"] = mode
                effective_payload["workspace"] = workspace_execution.request_workspace
                extra = _metadata_mapping(payload.get("extra"))
                if identity_provider_id == "codex-cli":
                    personalization = services.personalization_store.load()
                    extra["developer_instructions"] = (
                        personalization.developer_instructions
                    )
                    extra["personalization_revision"] = personalization.revision
                extra["native_source_workspace"] = source_workspace
                extra["workspace_execution"] = workspace_execution.to_metadata()
                effective_payload["extra"] = extra
            options = _native_process_start_options(
                payload=effective_payload,
                session=session,
                config=services.config,
                registry=services.registry,
                native_registry=services.native_registry,
                native_index_store=services.native_index_store,
                store=services.session_store,
                attachment_store=services.attachment_store,
            )
            if workspace_execution is None:
                workspace_metadata = _native_resume_workspace_execution(options)
            else:
                workspace_metadata = workspace_execution.to_metadata()
            requested_workspace_policy = (
                str(payload.get("workspace_policy") or "auto").strip().lower()
            )
            if (
                action == "resume"
                and options["mode"] == "edit"
                and (requested_workspace_policy in {"auto", "worktree"})
                and (workspace_metadata.get("policy") != "worktree")
            ):
                raise NativeProcessStartError(
                    "Native edit resume has no isolated worktree evidence; refusing to resume in the source checkout."
                )
            options["source_workspace"] = (
                workspace_metadata.get("source_workspace") or options["workspace"]
            )
            options["workspace_execution"] = workspace_metadata
            options["policy"] = policy_metadata
            if provider_account_binding is not None:
                options[PROVIDER_ACCOUNT_BINDING_KEY] = provider_account_binding
            options["plan"] = replace(
                options["plan"],
                metadata={
                    **dict(options["plan"].metadata),
                    "workspace_execution": workspace_metadata,
                    "policy": policy_metadata,
                },
            )
            run = services.session_store.create_run(
                run_id=run_id,
                session_id=session.id,
                harness_id=options["harness_id"],
                status="running",
                prompt=options["prompt"],
                model=options["model"],
                api_mode=options["api_mode"],
                capability=options["capability"],
                mode=options["mode"],
                workspace=options["workspace"],
                invocation_mode=HarnessInvocationMode.NATIVE,
                started_at=utc_now(),
                metadata=_native_process_run_metadata(options),
            )
            if options["action"] == "start" and options["prompt"]:
                services.session_store.append_message(
                    HarnessMessage(
                        id=new_id("msg"),
                        session_id=session.id,
                        run_id=run.id,
                        role="user",
                        content=options["prompt"],
                        created_at=utc_now(),
                        harness_id=options["harness_id"],
                        model=options["model"],
                        api_mode=options["api_mode"],
                    )
                )
            session_patch: dict[str, Any] = {
                "default_harness_id": options["harness_id"],
                "default_model": options["model"],
                "default_api_mode": options["api_mode"],
                "default_mode": options["mode"],
                "workspace": options["source_workspace"],
            }
            updated_session = services.session_store.update_session(
                session.id, **session_patch
            )
            if options["action"] == "resume" and isinstance(
                options.get("native_ref"), NativeSessionRef
            ):
                native_ref = options["native_ref"]
                services.session_runner.apply_native_session_title(
                    session_id=session.id,
                    run_id=run.id,
                    title=native_ref.title,
                    provider=native_ref.harness_id,
                    source_id=native_ref.native_session_id or native_ref.id,
                )
            elif options["action"] == "start" and options["prompt"]:
                services.session_runner.schedule_session_title(
                    updated_session,
                    run.id,
                    {
                        "prompt": options["prompt"],
                        "model": options["model"],
                        "extra": _metadata_mapping(effective_payload.get("extra")),
                    },
                )
            services.session_store.append_event(
                HarnessStoredEvent(
                    id=new_id("evt"),
                    session_id=session.id,
                    run_id=run.id,
                    type="policy_allowed",
                    message="Harness policy allowed native process spawn.",
                    payload=policy_metadata,
                    created_at=utc_now(),
                    trace_id=run.id,
                    span_kind="policy",
                    span_status="allowed",
                )
            )
            process_ref = services.native_process_manager.start(
                options["plan"],
                session_id=session.id,
                workspace=options["workspace"],
                run_id=run.id,
                timeout_seconds=_native_timeout_seconds(payload.get("timeout_seconds")),
            )
            if options["action"] == "start":
                recorder = getattr(options["connector"], "record_start_snapshot", None)
                if recorder is not None:
                    try:
                        recorder(options["plan"])
                    except (OSError, ValueError) as exc:
                        services.native_process_manager.stop(process_ref.id)
                        raise NativeProcessStartError(
                            "Could not persist native execution snapshot"
                        ) from exc
            run = services.session_store.update_run(
                run.id,
                command=process_ref.display_command,
                native_session_id=options["native_session_id"],
                metadata=_native_process_run_metadata(options, process_ref),
            )
            native_link = _append_native_process_link(
                store=services.session_store,
                session=session,
                options=options,
                process_ref=process_ref,
            )
            provenance = _build_current_run_provenance(
                store=services.session_store,
                registry=services.registry,
                config=services.config,
                run=run,
                runtime_store=services.runtime_store,
            )
            run = services.session_store.update_run(
                run.id,
                metadata={
                    **dict(run.metadata),
                    "provenance": run_provenance_to_dict(provenance),
                },
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except HTTPException:
            raise
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except (NativeProcessStartError, ValueError) as exc:
            if isinstance(options, Mapping):
                route_preflight = options.get("proxy_route_preflight")
                if isinstance(route_preflight, proxy.ProxyRoutePreflight):
                    proxy.stop_owned_sidecar(route_preflight.startup)
            if run is not None:
                services.session_store.update_run(
                    run.id,
                    status="failed",
                    error=str(exc),
                    finished_at=utc_now(),
                    metadata=_native_process_run_metadata(
                        options,
                        process_ref,
                        prompt_delivery_status=NativePromptDeliveryStatus.FAILED
                        if process_ref is None
                        else None,
                        prompt_delivery_error=str(exc) if process_ref is None else None,
                    ),
                )
            elif workspace_execution is not None and workspace_execution.worktree_path:
                with suppress(WorktreeError):
                    discard_run_worktree(
                        {"workspace_execution": workspace_execution.to_metadata()}
                    )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "process": native_process_ref_to_dict(process_ref),
            "run": run_to_dict(run),
            "native_link": native_link_to_dict(native_link),
        }

    return router
