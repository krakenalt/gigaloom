"""Routes extracted from the FastAPI composition root: catalog."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from fastapi import Body, HTTPException, Query
from gigaloom import proxy
from gigaloom.attachments import AttachmentNotFoundError
from gigaloom.config import pass_model_env_note
from gigaloom.ui.services.attachments import (
    route_recommendation_attachments as _route_recommendation_attachments,
    text_tuple as _text_tuple,
)
from gigaloom.ui.services.defaults import fallback_models as _fallback_models
from gigaloom.ui.services.request_values import optional_text as _optional_text
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.preflight import preflight_report_to_dict
from gigaloom.provider_account_sessions import ProviderAccountSessionError
from gigaloom.plugins import (
    harness_validation_report_to_dict,
    validate_harness_spec,
)
from gigaloom.routing import (
    recommend_harness_route,
    route_recommendation_to_dict,
)
from gigaloom.sessions import SessionNotFoundError
from gigaloom.cli_capabilities import cli_capability_snapshot_to_dict
from gigaloom.claude_handoff import (
    ClaudeHandoffError,
    claude_execution_surfaces_to_dict,
    claude_handoff_capability_to_dict,
)
from gigaloom.types import availability_to_dict, parse_api_mode, spec_to_dict
from gigaloom.ui.performance import ui_performance_budgets
from gigaloom.workbench_execution import (
    workbench_admission_projection,
    workbench_transport_projection,
)
from fastapi import APIRouter
from gigaloom.ui.container import AppServices


def create_router(services: AppServices) -> APIRouter:
    router = ContractAPIRouter()

    @router.fs_read.get("/api/harnesses")
    def harnesses() -> dict[str, Any]:
        harness_items = []
        for harness in services.registry.list():
            spec = harness.spec()
            validation = services.registry.validation_report(
                spec.id
            ) or validate_harness_spec(spec)
            capability_probe = getattr(harness, "capability_probe", None)
            provider_handoff_probe = getattr(
                harness, "provider_handoff_capability", None
            )
            provider_handoff = None
            execution_surfaces: list[dict[str, Any]] = []
            if callable(provider_handoff_probe):
                try:
                    handoff_capability = provider_handoff_probe()
                except ClaudeHandoffError:
                    handoff_capability = None
                if handoff_capability is not None:
                    provider_handoff = claude_handoff_capability_to_dict(
                        handoff_capability
                    )
                    execution_surfaces = claude_execution_surfaces_to_dict(
                        handoff_capability
                    )
            harness_items.append(
                {
                    "spec": spec_to_dict(spec),
                    "availability": availability_to_dict(harness.availability()),
                    "compatibility": cli_capability_snapshot_to_dict(capability_probe())
                    if callable(capability_probe)
                    else None,
                    "provider_handoff": provider_handoff,
                    "execution_surfaces": execution_surfaces,
                    "workbench_admission": workbench_admission_projection(harness),
                    "workbench_transport": workbench_transport_projection(harness),
                    "validation": harness_validation_report_to_dict(validation),
                }
            )
        return {
            "harnesses": harness_items,
            "discovery_errors": list(services.registry.discovery_errors),
        }

    @router.fs_read.get("/api/defaults")
    def defaults() -> dict[str, Any]:
        harness_defaults = services.settings_store.load().defaults
        return {
            "proxy_url": services.config.proxy_url,
            "default_harness_id": harness_defaults.default_harness_id,
            "default_model": harness_defaults.default_model,
            "default_api_mode": harness_defaults.default_api_mode,
            "default_mode": harness_defaults.mode,
            "task_intent": harness_defaults.task_intent,
            "authority": harness_defaults.authority,
            "execution_transport": harness_defaults.execution_transport,
            "invocation_mode": harness_defaults.invocation_mode,
            "workspace_policy": harness_defaults.workspace_policy,
            "permission_profile": harness_defaults.permission_profile,
            "stream": harness_defaults.stream,
            "auto_start_proxy": services.config.auto_start_proxy,
            "proxy_start_timeout_seconds": services.config.proxy_start_timeout_seconds,
            "note": pass_model_env_note(),
            "performance_budgets": ui_performance_budgets(),
        }

    @router.net_read.get("/api/models")
    def models(api_mode: str = Query(default="v2")) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        try:
            mode = parse_api_mode(api_mode)
        except ValueError:
            return {
                "schema_version": 1,
                "ok": False,
                "api_mode": None,
                "route_path": None,
                "health": "unknown",
                "last_checked_at": checked_at,
                "models": _fallback_models(services.config),
                "source": "fallback",
                "error": "invalid api_mode; expected v1 or v2",
                "note": pass_model_env_note(),
            }
        try:
            discovery = proxy.discover_models(
                services.config,
                mode,
                include_compat_paths=False,
                include_fallback=False,
            )
        except Exception:
            return {
                "schema_version": 1,
                "ok": False,
                "api_mode": mode.value,
                "route_path": f"/{mode.value}/models",
                "health": "unknown",
                "last_checked_at": checked_at,
                "models": [],
                "source": f"/{mode.value}/models",
                "error": "model discovery failed",
                "note": pass_model_env_note(),
            }
        return {
            "schema_version": 1,
            "ok": discovery.ok,
            "api_mode": mode.value,
            "route_path": f"/{mode.value}/models",
            "health": "ready" if discovery.ok else "blocked",
            "last_checked_at": checked_at,
            "models": list(discovery.models[:100]),
            "source": discovery.source,
            "error": None if discovery.ok else "model discovery failed",
            "note": pass_model_env_note(),
        }

    @router.net_read.get("/api/health")
    def health() -> dict[str, Any]:
        status = proxy.health_check(services.config)
        return {
            "ok": status.ok,
            "proxy_url": status.url,
            "path": status.path,
            "status_code": status.status_code,
            "error": status.error,
            "async_data_plane": services.async_diagnostics.snapshot(),
            "event_streams": services.run_event_broker.snapshot(),
        }

    @router.fs_read.post("/api/preflight/run")
    def preflight_run(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        durable = bool(
            services.job_dispatcher is not None
            and str(payload.get("invocation_mode") or "headless") != "native"
        )
        if payload.get("durable") is False:
            durable = False
        try:
            prepared = services.session_service.prepare_turn_payload(
                payload, session_id=_optional_text(payload.get("session_id"))
            )
            report = services.session_runner.preflight(
                prepared,
                session_id=_optional_text(payload.get("session_id")),
                durable=durable,
            )
        except SessionNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        except ProviderAccountSessionError as exc:
            raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"preflight": preflight_report_to_dict(report)}

    @router.fs_read.post("/api/route/recommendation")
    def route_recommendation(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        try:
            attachments = _route_recommendation_attachments(
                payload, attachment_store=services.attachment_store
            )
            recommendation = recommend_harness_route(
                services.registry,
                prompt=str(payload.get("prompt") or ""),
                mode=_optional_text(payload.get("mode")),
                workspace=_optional_text(payload.get("workspace")),
                attachments=attachments,
                selected_files=_text_tuple(payload.get("selected_files")),
            )
        except AttachmentNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Attachment not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"recommendation": route_recommendation_to_dict(recommendation)}

    return router
