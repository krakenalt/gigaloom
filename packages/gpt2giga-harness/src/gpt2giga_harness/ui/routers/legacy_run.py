"""Legacy Run domain routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from gpt2giga_harness.preflight import (
    build_preflight_report,
    format_preflight_block_message,
)
from gpt2giga_harness.types import (
    GigaChatApiMode,
    HarnessCapability,
    HarnessRequest,
    parse_api_mode,
    parse_builtin_tools,
    parse_capability,
    result_to_dict,
)
from gpt2giga_harness.ui.async_execution import ContractAPIRouter
from gpt2giga_harness.ui.container import AppServices
from gpt2giga_harness.ui.services.request_values import optional_text as _optional_text
from gpt2giga_harness.workspace import resolve_workspace


def create_router(services: AppServices) -> APIRouter:
    """Create the legacy run router."""
    router = ContractAPIRouter()

    @router.proc.post("/api/run")
    def run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        harness_id = str(payload.get("harness_id") or "echo")
        try:
            harness = services.registry.get(harness_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown harness") from exc
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        extra = dict(extra)
        if bool(payload.get("dry_run")):
            extra["dry_run"] = True
        try:
            api_mode = parse_api_mode(payload.get("api_mode"))
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Invalid api_mode; expected v1 or v2"
            ) from exc
        try:
            capability = parse_capability(
                payload.get("capability") or HarnessCapability.CHAT_COMPLETIONS.value
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid capability") from exc
        try:
            builtin_tools = parse_builtin_tools(payload.get("builtin_tools"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if builtin_tools and api_mode is not GigaChatApiMode.V2:
            raise HTTPException(
                status_code=400, detail="built-in tools require /v2/chat/completions"
            )
        unsupported_builtin_tools = [
            tool.value
            for tool in builtin_tools
            if tool not in set(getattr(harness.spec(), "supported_builtin_tools", ()))
        ]
        if unsupported_builtin_tools:
            raise HTTPException(
                status_code=400,
                detail=f"{harness_id} does not support built-in tools: "
                + ", ".join(unsupported_builtin_tools),
            )
        request = HarnessRequest(
            prompt=str(payload.get("prompt") or ""),
            model=_optional_text(payload.get("model")),
            api_mode=api_mode,
            capability=capability,
            mode=str(payload.get("mode") or "plan"),
            stream=bool(payload.get("stream")),
            workspace=resolve_workspace(_optional_text(payload.get("workspace"))),
            builtin_tools=builtin_tools,
            extra=extra,
        )
        preflight = build_preflight_report(
            prompt=request.prompt,
            workspace=request.workspace,
            data_dir=services.config.data_dir,
        )
        if preflight.hard_block:
            raise HTTPException(
                status_code=400, detail=format_preflight_block_message(preflight)
            )
        try:
            result = harness.run(request, services.config.to_context())
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Harness run failed") from exc
        return result_to_dict(result)

    return router
