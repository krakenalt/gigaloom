"""Backend-authoritative, redaction-safe Cockpit Settings APIs."""

from __future__ import annotations

from dataclasses import asdict
from threading import Lock
from typing import Any, Mapping

from fastapi import Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from gigaloom.diagnostics.doctor.report import build_doctor_report
from gigaloom.diagnostics.inventory.capabilities import (
    AuthorityLevel,
    TaskIntent,
    legacy_mode_compatibility_receipt,
)
from gigaloom.provider_settings import (
    ProviderRegistryConflict,
    ProviderSettingsNotFoundError,
    ProviderSettingsService,
    ProviderSettingsValidationError,
)
from gigaloom.provider_authentication_broker import (
    ProviderAuthenticationConflictError,
    ProviderAuthenticationOperationError,
    provider_account_snapshot_to_dict,
)
from gigaloom.runtime.policy import permission_profile
from gigaloom.settings import (
    SETTINGS_FIELDS,
    HarnessDefaultsSnapshot,
    SettingsConflictError,
)
from gigaloom.types import parse_api_mode
from gigaloom.ui.async_execution import ContractAPIRouter
from gigaloom.ui.services.settings_snapshots import SettingsSnapshotService


router = ContractAPIRouter()
_SETTINGS_SERVICE_LOCK = Lock()


@router.fs_read.get("/api/doctor")
def doctor_read_model(request: Request) -> dict[str, Any]:
    """Return one guided content-free doctor snapshot without online probes."""
    security = request.app.state.harness_ui_security
    if security.local_mode:
        status = security.local_status(request)
        identity = {
            "local": True,
            "authenticated": status.authenticated,
            "claimable": status.claimable,
        }
    else:
        session = security.remote_session(request)
        identity = {
            "local": False,
            "authenticated": session is not None,
            "claimable": False,
            "role": session.actor.role if session is not None else None,
        }
    return build_doctor_report(
        request.app.state.harness_config,
        request.app.state.harness_registry,
        workspace=None,
        online_checks=False,
        ui_identity=identity,
    )


@router.fs_read.get("/api/settings")
def settings_read_model(
    request: Request,
    workspace: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return bounded settings categories without credentials or raw paths."""
    try:
        return _settings_service(request).legacy(workspace)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.fs_read.get("/api/settings/summary")
def settings_summary(
    request: Request,
    workspace: str | None = Query(default=None),
) -> Response:
    """Return lightweight source revisions for independently loaded sections."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).summary(workspace),
    )


@router.fs_read.get("/api/settings/runtime")
def settings_runtime(request: Request) -> Response:
    """Return immutable runtime configuration without a health probe."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).runtime(),
    )


@router.fs_read.get("/api/settings/defaults")
def settings_defaults(request: Request) -> Response:
    """Return defaults and static harness metadata without executable probes."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).defaults(),
    )


@router.fs_read.get("/api/settings/workspace")
def settings_workspace(
    request: Request,
    workspace: str | None = Query(default=None),
) -> Response:
    """Return the selected workspace projection on demand."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).workspace(workspace),
    )


@router.fs_read.get("/api/settings/mcp")
def settings_mcp(
    request: Request,
    workspace: str | None = Query(default=None),
) -> Response:
    """Return bounded MCP inventory and history evidence on demand."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).mcp(workspace),
    )


@router.fs_read.get("/api/settings/diagnostics")
def settings_diagnostics(request: Request) -> Response:
    """Return current content-free in-memory diagnostics on demand."""
    return _settings_section_response(
        request,
        lambda: _settings_service(request).diagnostics(),
    )


@router.fs_read.get("/api/providers")
def list_provider_settings(request: Request) -> dict[str, Any]:
    """Return the reference-only provider registry and template catalog."""
    return request.app.state.harness_provider_settings_service.list()


@router.proc_read.get("/api/provider-accounts")
def list_provider_accounts(request: Request) -> dict[str, Any]:
    """Return typed provider-owned account cards from isolated homes."""
    return request.app.state.harness_native_login_broker.list_accounts()


@router.proc_read.post("/api/provider-accounts/{provider_id}/refresh")
def refresh_provider_account(request: Request, provider_id: str) -> dict[str, Any]:
    """Explicitly refresh one provider-owned status projection."""
    return _provider_account_action(request, provider_id, "refresh")


@router.proc.post("/api/provider-accounts/{provider_id}/login")
def start_provider_login(request: Request, provider_id: str) -> dict[str, Any]:
    """Start one bounded provider-owned login attempt."""
    return _provider_account_action(request, provider_id, "start")


@router.proc.post("/api/provider-accounts/{provider_id}/cancel")
def cancel_provider_login(request: Request, provider_id: str) -> dict[str, Any]:
    """Cancel the exact pending provider-owned login attempt."""
    return _provider_account_action(request, provider_id, "cancel")


@router.proc.post("/api/provider-accounts/{provider_id}/logout")
def logout_provider_account(request: Request, provider_id: str) -> dict[str, Any]:
    """Run the reviewed provider-owned logout operation."""
    return _provider_account_action(request, provider_id, "logout")


@router.fs_read.get("/api/providers/{provider_id}")
def get_provider_settings(request: Request, provider_id: str) -> dict[str, Any]:
    """Return one backend-owned provider projection."""
    try:
        return request.app.state.harness_provider_settings_service.get(provider_id)
    except ProviderSettingsNotFoundError as exc:
        raise HTTPException(status_code=404, detail="provider not found") from exc
    except ProviderSettingsValidationError as exc:
        raise _provider_field_error(exc) from exc


@router.fs_atomic.post("/api/providers")
def create_provider_settings(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Create and authoritatively read back one reference-only provider."""
    provider_id = payload.get("id")
    spec = {key: value for key, value in payload.items() if key != "id"}
    try:
        result = request.app.state.harness_provider_settings_service.create(
            provider_id,
            spec,
        )
    except ProviderSettingsValidationError as exc:
        raise _provider_field_error(exc) from exc
    except ProviderRegistryConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "provider_conflict", "message": str(exc)},
        ) from exc
    return {"saved": True, "provider": result.provider, "effects": result.effects}


@router.fs_atomic.patch("/api/providers/{provider_id}")
def update_provider_settings(
    request: Request,
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Optimistically edit and read back one reference-only provider."""
    expected_revision = payload.get("expected_revision")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise _field_error({"expected_revision": "expected an integer revision"})
    spec = {key: value for key, value in payload.items() if key != "expected_revision"}
    try:
        result = request.app.state.harness_provider_settings_service.update(
            provider_id,
            spec,
            expected_revision=expected_revision,
        )
    except ProviderSettingsNotFoundError as exc:
        raise HTTPException(status_code=404, detail="provider not found") from exc
    except ProviderSettingsValidationError as exc:
        raise _provider_field_error(exc) from exc
    except ProviderRegistryConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "provider_conflict", "message": str(exc)},
        ) from exc
    return {"saved": True, "provider": result.provider, "effects": result.effects}


@router.net_atomic.post("/api/providers/{provider_id}/test")
def test_provider_settings(request: Request, provider_id: str) -> dict[str, Any]:
    """Run one explicit bounded provider connection check."""
    return _run_provider_check(request, provider_id, discover_models=False)


@router.net_atomic.post("/api/providers/{provider_id}/discover")
def discover_provider_models(request: Request, provider_id: str) -> dict[str, Any]:
    """Run one explicit bounded provider model-discovery check."""
    return _run_provider_check(request, provider_id, discover_models=True)


@router.fs_atomic.patch("/api/settings/defaults")
def update_settings_defaults(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """Validate, atomically persist, and read back new-run defaults."""
    store = request.app.state.harness_settings_store
    current = store.load()
    patch = payload.get("defaults", payload)
    if not isinstance(patch, Mapping):
        raise _field_error({"defaults": "expected an object"})
    expected_revision = payload.get("expected_revision")
    unknown = sorted(set(patch) - SETTINGS_FIELDS - {"expected_revision"})
    if unknown:
        raise _field_error({field: "unknown setting" for field in unknown})
    normalized_patch = dict(patch)
    candidate = {**asdict(current.defaults), **normalized_patch}
    if {"task_intent", "authority"} & set(normalized_patch):
        mode = _mode_for_product_defaults(
            candidate.get("task_intent"),
            candidate.get("authority"),
        )
        if mode is not None:
            normalized_patch["mode"] = mode
    elif "mode" in normalized_patch:
        legacy = legacy_mode_compatibility_receipt(normalized_patch["mode"])
        normalized_patch["task_intent"] = legacy["intent"]
        normalized_patch["authority"] = legacy["authority"]
    locked = set(current.locked_fields)
    locked_changes = {
        field: "owned by the environment; restart with a new environment value"
        for field in normalized_patch
        if field in locked
        and getattr(current.defaults, field) != normalized_patch[field]
    }
    if locked_changes:
        raise HTTPException(
            status_code=409,
            detail={"code": "environment_owned", "field_errors": locked_changes},
        )
    values = {**asdict(current.defaults), **normalized_patch}
    field_errors = _validate_defaults(request, values)
    if field_errors:
        raise _field_error(field_errors)
    try:
        saved = store.save(values, expected_revision=_optional_text(expected_revision))
    except SettingsConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "revision_conflict", "message": str(exc)},
        ) from exc
    return _saved_defaults(saved)


def _validate_defaults(request: Request, values: Mapping[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    harness_id = _optional_text(values.get("default_harness_id"))
    harness = None
    if harness_id is None:
        errors["default_harness_id"] = "select a harness"
    else:
        try:
            harness = request.app.state.harness_registry.get(harness_id)
        except KeyError:
            errors["default_harness_id"] = "unknown harness"
    try:
        api_mode = parse_api_mode(values.get("default_api_mode"))
    except ValueError:
        errors["default_api_mode"] = "expected v1 or v2"
        api_mode = None
    for field in ("default_model", "default_title_model"):
        model = values.get(field)
        if model is not None and (
            not isinstance(model, str)
            or not model.strip()
            or len(model.strip()) > 200
            or any(ord(character) < 32 for character in model)
        ):
            errors[field] = "expected a non-empty model name up to 200 characters"
    invocation = values.get("invocation_mode")
    if invocation not in {"headless", "native"}:
        errors["invocation_mode"] = "expected headless or native"
    elif invocation == "native" and harness is not None:
        if not harness.spec().supports_native_sessions:
            errors["invocation_mode"] = (
                "selected harness does not support native sessions"
            )
    transport = values.get("execution_transport")
    if transport not in {"native_structured", "native_terminal", "one_shot"}:
        errors["execution_transport"] = (
            "expected native_structured, native_terminal, or one_shot"
        )
    elif transport == "native_terminal" and harness is not None:
        if not harness.spec().supports_native_sessions:
            errors["execution_transport"] = (
                "selected harness does not support native terminal sessions"
            )
    if transport == "native_terminal" and invocation != "native":
        errors.setdefault(
            "invocation_mode", "native_terminal requires native invocation"
        )
    elif transport in {"native_structured", "one_shot"} and invocation != "headless":
        errors.setdefault(
            "invocation_mode", f"{transport} requires headless invocation"
        )
    if values.get("task_intent") not in {item.value for item in TaskIntent}:
        errors["task_intent"] = "expected ask, review, or change"
    if values.get("authority") not in {item.value for item in AuthorityLevel}:
        errors["authority"] = "expected read_only or workspace_write"
    if values.get("mode") not in {"plan", "read", "edit", "act"}:
        errors["mode"] = "expected plan, read, or edit"
    if values.get("workspace_policy") not in {"auto", "current", "worktree"}:
        errors["workspace_policy"] = "expected auto, current, or worktree"
    try:
        permission_profile(values.get("permission_profile"), origin="manual")
    except ValueError as exc:
        errors["permission_profile"] = str(exc)
    if not isinstance(values.get("stream"), bool):
        errors["stream"] = "expected true or false"
    if harness is not None and api_mode is not None:
        spec = harness.spec()
        if not spec.supports_api_mode_selection and api_mode.value != "v2":
            errors["default_api_mode"] = "selected harness fixes its API mode"
    return errors


def _saved_defaults(snapshot: HarnessDefaultsSnapshot) -> dict[str, Any]:
    return {
        "saved": True,
        "revision": snapshot.revision,
        "defaults": asdict(snapshot.defaults),
        "sources": dict(snapshot.sources),
        "locked_fields": list(snapshot.locked_fields),
        "change_effect": "new_runs",
    }


def _mode_for_product_defaults(intent: Any, authority: Any) -> str | None:
    """Project explicit product defaults onto the retained machine alias."""
    if intent == TaskIntent.ASK.value:
        return "plan"
    if intent == TaskIntent.REVIEW.value:
        return "read"
    if intent == TaskIntent.CHANGE.value:
        return "edit" if authority == AuthorityLevel.WORKSPACE_WRITE.value else "read"
    return None


def _field_error(errors: Mapping[str, str]) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "invalid_settings", "field_errors": dict(errors)},
    )


def _provider_field_error(exc: ProviderSettingsValidationError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": "invalid_provider", "field_errors": exc.field_errors},
    )


def _run_provider_check(
    request: Request,
    provider_id: str,
    *,
    discover_models: bool,
) -> dict[str, Any]:
    service: ProviderSettingsService = (
        request.app.state.harness_provider_settings_service
    )
    try:
        return service.check(provider_id, discover_models=discover_models)
    except ProviderSettingsNotFoundError as exc:
        raise HTTPException(status_code=404, detail="provider not found") from exc
    except ProviderSettingsValidationError as exc:
        raise _provider_field_error(exc) from exc


def _provider_account_action(
    request: Request,
    provider_id: str,
    operation: str,
) -> dict[str, Any]:
    broker = request.app.state.harness_native_login_broker
    try:
        snapshot = getattr(broker, operation)(provider_id)
    except ProviderAuthenticationConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "provider_login_conflict", "message": str(exc)},
        ) from exc
    except ProviderAuthenticationOperationError as exc:
        reason_code = str(exc)
        status_code = 404 if reason_code == "provider_authentication_unknown" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": reason_code},
        ) from exc
    return {"account": provider_account_snapshot_to_dict(snapshot)}


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _settings_service(request: Request) -> SettingsSnapshotService:
    service = getattr(request.app.state, "harness_settings_snapshot_service", None)
    if isinstance(service, SettingsSnapshotService):
        return service
    with _SETTINGS_SERVICE_LOCK:
        service = getattr(
            request.app.state,
            "harness_settings_snapshot_service",
            None,
        )
        if isinstance(service, SettingsSnapshotService):
            return service
        service = SettingsSnapshotService(
            config=request.app.state.harness_config,
            settings_store=request.app.state.harness_settings_store,
            provider_settings_service=(
                request.app.state.harness_provider_settings_service
            ),
            registry=request.app.state.harness_registry,
            async_diagnostics=request.app.state.harness_async_diagnostics,
        )
        request.app.state.harness_settings_snapshot_service = service
        return service


def _settings_section_response(
    request: Request,
    load: Any,
) -> Response:
    try:
        payload = load()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    revision = str(payload["revision"])
    etag = f'"{revision}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )
    return JSONResponse(
        content=payload,
        headers={"ETag": etag, "Cache-Control": "private, no-cache"},
    )
