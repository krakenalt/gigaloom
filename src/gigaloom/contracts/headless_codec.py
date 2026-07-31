"""Strict canonical codecs for headless invocation and event contracts."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, TypeVar, cast

from gigaloom.contracts.headless import (
    HeadlessCapsuleMode,
    HeadlessEventFormat,
    HeadlessEventKind,
    HeadlessEventV1,
    HeadlessExitCode,
    HeadlessInvocationV1,
    HeadlessPromptSourceKind,
    HeadlessPromptSourceV1,
    HeadlessTerminalReceiptV1,
)
from gigaloom.contracts.operational_validation import (
    parse_timestamp,
    require_mapping,
    thaw_json,
)


_EnumT = TypeVar("_EnumT", bound=Enum)


def headless_prompt_source_to_dict(
    value: HeadlessPromptSourceV1,
) -> dict[str, Any]:
    """Serialize one content-free prompt-source binding."""
    return {
        "schema_version": value.schema_version,
        "kind": value.kind.value,
        "content_digest": value.content_digest,
        "reference": value.reference,
    }


def headless_prompt_source_from_dict(
    payload: Mapping[str, Any],
) -> HeadlessPromptSourceV1:
    """Decode one strict prompt-source binding."""
    value = require_mapping(
        payload,
        required={"schema_version", "kind", "content_digest", "reference"},
        field_name="headless prompt source",
    )
    return HeadlessPromptSourceV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        kind=_enum(HeadlessPromptSourceKind, value["kind"], "kind"),
        content_digest=_string(value["content_digest"], "content_digest"),
        reference=_optional_string(value["reference"], "reference"),
    )


def headless_invocation_to_dict(value: HeadlessInvocationV1) -> dict[str, Any]:
    """Serialize one deterministic headless invocation."""
    return {
        "schema_version": value.schema_version,
        "run_id": value.run_id,
        "agent_id": value.agent_id,
        "route_id": value.route_id,
        "model_id": value.model_id,
        "workspace": value.workspace,
        "prompt_source": headless_prompt_source_to_dict(value.prompt_source),
        "result_dir": value.result_dir,
        "event_format": value.event_format.value,
        "timeout_seconds": value.timeout_seconds,
        "permission_profile": value.permission_profile,
        "network_profile": value.network_profile,
        "capsule_mode": value.capsule_mode.value,
        "environment_contract_digest": value.environment_contract_digest,
        "no_input": value.no_input,
    }


def headless_invocation_from_dict(
    payload: Mapping[str, Any],
) -> HeadlessInvocationV1:
    """Decode a strict non-interactive invocation."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "run_id",
            "agent_id",
            "route_id",
            "model_id",
            "workspace",
            "prompt_source",
            "result_dir",
            "event_format",
            "timeout_seconds",
            "permission_profile",
            "network_profile",
            "capsule_mode",
            "environment_contract_digest",
            "no_input",
        },
        field_name="headless invocation",
    )
    prompt_payload = _mapping(value["prompt_source"], "prompt_source")
    return HeadlessInvocationV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        run_id=_string(value["run_id"], "run_id"),
        agent_id=_string(value["agent_id"], "agent_id"),
        route_id=_string(value["route_id"], "route_id"),
        model_id=_string(value["model_id"], "model_id"),
        workspace=_string(value["workspace"], "workspace"),
        prompt_source=headless_prompt_source_from_dict(prompt_payload),
        result_dir=_string(value["result_dir"], "result_dir"),
        event_format=_enum(
            HeadlessEventFormat,
            value["event_format"],
            "event_format",
        ),
        timeout_seconds=_integer(value["timeout_seconds"], "timeout_seconds"),
        permission_profile=_string(
            value["permission_profile"],
            "permission_profile",
        ),
        network_profile=_string(value["network_profile"], "network_profile"),
        capsule_mode=_enum(HeadlessCapsuleMode, value["capsule_mode"], "capsule_mode"),
        environment_contract_digest=_string(
            value["environment_contract_digest"],
            "environment_contract_digest",
        ),
        no_input=_boolean(value["no_input"], "no_input"),
    )


def headless_event_to_dict(value: HeadlessEventV1) -> dict[str, Any]:
    """Serialize one canonical JSONL event object."""
    return {
        "schema_version": value.schema_version,
        "sequence": value.sequence,
        "run_id": value.run_id,
        "timestamp": value.timestamp.isoformat(),
        "kind": value.kind.value,
        "payload": thaw_json(value.payload),
        "content_capture": value.content_capture,
    }


def headless_event_from_dict(payload: Mapping[str, Any]) -> HeadlessEventV1:
    """Decode one strict canonical JSONL event object."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "sequence",
            "run_id",
            "timestamp",
            "kind",
            "payload",
            "content_capture",
        },
        field_name="headless event",
    )
    return HeadlessEventV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        sequence=_integer(value["sequence"], "sequence"),
        run_id=_string(value["run_id"], "run_id"),
        timestamp=parse_timestamp(value["timestamp"], field_name="timestamp"),
        kind=_enum(HeadlessEventKind, value["kind"], "kind"),
        payload=_mapping(value["payload"], "payload"),
        content_capture=_boolean(value["content_capture"], "content_capture"),
    )


def headless_terminal_receipt_to_dict(
    value: HeadlessTerminalReceiptV1,
) -> dict[str, Any]:
    """Serialize terminal evidence required beside the process exit code."""
    return {
        "schema_version": value.schema_version,
        "receipt_id": value.receipt_id,
        "run_id": value.run_id,
        "terminal_kind": value.terminal_kind.value,
        "final_sequence": value.final_sequence,
        "exit_code": int(value.exit_code),
        "result_ref": value.result_ref,
        "capsule_ref": value.capsule_ref,
        "omissions": list(value.omissions),
        "finished_at": value.finished_at.isoformat(),
        "content_free": value.content_free,
    }


def headless_terminal_receipt_from_dict(
    payload: Mapping[str, Any],
) -> HeadlessTerminalReceiptV1:
    """Decode one strict terminal receipt."""
    value = require_mapping(
        payload,
        required={
            "schema_version",
            "receipt_id",
            "run_id",
            "terminal_kind",
            "final_sequence",
            "exit_code",
            "result_ref",
            "capsule_ref",
            "omissions",
            "finished_at",
            "content_free",
        },
        field_name="headless terminal receipt",
    )
    exit_code = _integer(value["exit_code"], "exit_code")
    try:
        parsed_exit = HeadlessExitCode(exit_code)
    except ValueError as error:
        raise ValueError("headless exit_code is invalid") from error
    return HeadlessTerminalReceiptV1(
        schema_version=_integer(value["schema_version"], "schema_version"),
        receipt_id=_string(value["receipt_id"], "receipt_id"),
        run_id=_string(value["run_id"], "run_id"),
        terminal_kind=_enum(
            HeadlessEventKind,
            value["terminal_kind"],
            "terminal_kind",
        ),
        final_sequence=_integer(value["final_sequence"], "final_sequence"),
        exit_code=parsed_exit,
        result_ref=_optional_string(value["result_ref"], "result_ref"),
        capsule_ref=_optional_string(value["capsule_ref"], "capsule_ref"),
        omissions=_string_tuple(value["omissions"], "omissions"),
        finished_at=parse_timestamp(value["finished_at"], field_name="finished_at"),
        content_free=_boolean(value["content_free"], "content_free"),
    )


def _enum(enum_type: type[_EnumT], value: object, field_name: str) -> _EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return cast(Mapping[str, Any], value)


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string(value, field_name)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be an array of strings")
    return tuple(cast(str, item) for item in value)


def _integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
