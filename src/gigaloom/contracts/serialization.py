"""Stable redaction-safe serialization for public harness contracts."""

from __future__ import annotations

from typing import Any, Mapping

from gigaloom.contracts.events import HarnessEvent
from gigaloom.contracts.execution import HarnessInvocationMode
from gigaloom.contracts.harness import (
    AdapterCapabilitySupport,
    AdapterSupportLevel,
    AttachmentTransportSupport,
    Availability,
    HarnessCapability,
    HarnessResult,
    HarnessSpec,
    HeadlessContinuationStrategy,
)
from gigaloom.contracts.providers import (
    GigaChatApiMode,
    GigaChatBuiltinTool,
)
from gigaloom.core.redaction import redact_secrets


def availability_to_dict(availability: Availability) -> dict[str, Any]:
    """Serialize availability for JSON output."""
    return {
        "status": availability.status.value,
        "reason": availability.reason,
        "detail": availability.detail,
    }


def spec_to_dict(spec: HarnessSpec) -> dict[str, Any]:
    """Serialize a harness spec for JSON output."""
    capabilities = list(spec_capability_values(spec))
    config_schema = _safe_mapping(getattr(spec, "config_schema", {}))
    metadata = _safe_mapping(getattr(spec, "metadata", {}))
    accepted_attachment_kinds = _string_values(
        getattr(spec, "accepted_attachment_kinds", ())
    )
    attachment_transport = _string_values(getattr(spec, "attachment_transport", ()))
    attachment_capabilities = _attachment_capabilities_to_dict(
        getattr(spec, "attachment_capabilities", {})
    )
    supported_builtin_tools = [
        tool.value if isinstance(tool, GigaChatBuiltinTool) else str(tool)
        for tool in getattr(spec, "supported_builtin_tools", ())
    ]
    default_invocation_mode = _enum_text(
        getattr(spec, "default_invocation_mode", None),
        HarnessInvocationMode.HEADLESS.value,
    )
    default_api_mode = _enum_text(
        getattr(spec, "default_api_mode", None),
        GigaChatApiMode.V2.value,
    )
    tags = _string_values(getattr(spec, "tags", ()))
    protocol_capability_scope = (
        _optional_text(getattr(spec, "protocol_capability_scope", None))
        or "harness_surface"
    )
    headless_continuation = _enum_text(
        getattr(spec, "headless_continuation", None),
        HeadlessContinuationStrategy.ONE_SHOT.value,
    )
    adapter_capabilities = _adapter_capabilities_to_dict(
        getattr(spec, "adapter_capabilities", {})
    )
    return {
        "id": _optional_text(spec.id) or "",
        "title": _optional_text(spec.title) or "",
        "kind": _optional_text(spec.kind) or "",
        "description": _optional_text(spec.description) or "",
        "icon": _optional_text(getattr(spec, "icon", None)),
        "capabilities": capabilities,
        "supports_model_selection": spec.supports_model_selection,
        "supports_api_mode_selection": spec.supports_api_mode_selection,
        "supports_streaming": spec.supports_streaming,
        "supports_structured_events": spec.supports_structured_events,
        "supports_cancellation": spec.supports_cancellation,
        "supports_workspace": spec.supports_workspace,
        "supports_attachments": spec.supports_attachments,
        "accepted_attachment_kinds": accepted_attachment_kinds,
        "attachment_transport": attachment_transport,
        "attachment_capabilities": attachment_capabilities,
        "supports_native_sessions": spec.supports_native_sessions,
        "supports_external_history": spec.supports_external_history,
        "supported_builtin_tools": supported_builtin_tools,
        "default_invocation_mode": default_invocation_mode,
        "default_api_mode": default_api_mode,
        "protocol_capability_scope": protocol_capability_scope,
        "headless_continuation": headless_continuation,
        "adapter_capabilities": adapter_capabilities,
        "tags": tags,
        "config_schema": config_schema,
        "metadata": metadata,
        "plugin_metadata": {
            "display_name": _optional_text(spec.title) or "",
            "description": _optional_text(spec.description) or "",
            "icon": _optional_text(getattr(spec, "icon", None)),
            "kind": _optional_text(spec.kind) or "",
            "capabilities": capabilities,
            "supports": {
                "model_selection": spec.supports_model_selection,
                "api_mode_selection": spec.supports_api_mode_selection,
                "streaming": spec.supports_streaming,
                "structured_events": spec.supports_structured_events,
                "cancellation": spec.supports_cancellation,
                "workspace": spec.supports_workspace,
                "attachments": spec.supports_attachments,
                "native_sessions": spec.supports_native_sessions,
                "external_history": spec.supports_external_history,
                "builtin_tools": bool(supported_builtin_tools),
                "headless": True,
                "native": spec.supports_native_sessions,
            },
            "attachments": {
                "supported": spec.supports_attachments,
                "accepted_kinds": accepted_attachment_kinds,
                "transport": attachment_transport,
                "capabilities": attachment_capabilities,
            },
            "builtin_tools": supported_builtin_tools,
            "protocol_capability_scope": protocol_capability_scope,
            "headless_continuation": headless_continuation,
            "adapter_capabilities": adapter_capabilities,
            "config_schema": config_schema,
            "metadata": metadata,
        },
    }


def spec_capability_values(spec: HarnessSpec) -> tuple[str, ...]:
    """Return known capability values from a spec, ignoring unknown plugin fields."""
    values: list[str] = []
    for capability in getattr(spec, "capabilities", ()):
        value = _capability_value(capability)
        if value is not None and value not in values:
            values.append(value)
    return tuple(values)


def _capability_value(value: Any) -> str | None:
    if isinstance(value, HarnessCapability):
        return value.value
    if isinstance(value, str):
        try:
            return HarnessCapability(value).value
        except ValueError:
            return None
    return None


def _safe_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(redact_secrets(dict(value)))


def _adapter_capabilities_to_dict(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping):
        return {}
    serialized: dict[str, dict[str, str]] = {}
    for raw_name, raw_support in value.items():
        name = _optional_text(raw_name)
        if name is None:
            continue
        if isinstance(raw_support, AdapterCapabilitySupport):
            status = raw_support.status
            detail = raw_support.detail
        elif isinstance(raw_support, Mapping):
            try:
                status = AdapterSupportLevel(str(raw_support.get("status") or ""))
            except ValueError:
                continue
            detail = str(raw_support.get("detail") or "")
        else:
            try:
                status = AdapterSupportLevel(str(raw_support))
            except ValueError:
                continue
            detail = ""
        serialized[name] = {
            "status": status.value,
            "detail": str(redact_secrets(detail)),
        }
    return serialized


def _attachment_capabilities_to_dict(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        return {}
    serialized: dict[str, dict[str, Any]] = {}
    for raw_kind, raw_support in value.items():
        kind = _optional_text(raw_kind)
        if kind is None:
            continue
        if isinstance(raw_support, AttachmentTransportSupport):
            headless = raw_support.headless
            native = raw_support.native
            rich = raw_support.rich
            required = raw_support.required_cli_capabilities
            detail = raw_support.detail
        elif isinstance(raw_support, Mapping):
            headless = raw_support.get("headless", ())
            native = raw_support.get("native", ())
            rich = bool(raw_support.get("rich", False))
            required = raw_support.get("required_cli_capabilities", ())
            detail = str(raw_support.get("detail") or "")
        else:
            continue
        serialized[kind] = {
            "headless": _string_values(headless),
            "native": _string_values(native),
            "rich": bool(rich),
            "required_cli_capabilities": _string_values(required),
            "detail": str(redact_secrets(detail)),
        }
    return serialized


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def _enum_text(value: Any, default: str) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    if isinstance(value, str) and value:
        return value
    return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def event_to_dict(event: HarnessEvent) -> dict[str, Any]:
    """Serialize a harness event for JSON output."""
    return {
        "type": event.type,
        "message": event.message,
        "payload": redact_secrets(dict(event.payload)),
    }


def result_to_dict(result: HarnessResult) -> dict[str, Any]:
    """Serialize a harness result without exposing secrets."""
    return {
        "ok": result.ok,
        "text": redact_secrets(result.text),
        "raw": redact_secrets(dict(result.raw)),
        "events": [event_to_dict(event) for event in result.events],
        "command": redact_secrets(list(result.command)),
        "error": redact_secrets(result.error),
    }


for _public_contract in (
    availability_to_dict,
    event_to_dict,
    result_to_dict,
    spec_capability_values,
    spec_to_dict,
):
    _public_contract.__module__ = "gigaloom.types"

__all__ = [
    "availability_to_dict",
    "event_to_dict",
    "result_to_dict",
    "spec_capability_values",
    "spec_to_dict",
]
