"""Codex CLI harness for running Codex through local gpt2giga."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from gigaloom import proxy
from gigaloom.contracts.codex_instructions import codex_developer_instructions
from gigaloom.harnesses.attachment_plan import (
    prompt_with_attachments,
    request_render_plan,
)
from gigaloom.managed_mcp import (
    write_startup_config,
)
from gigaloom.types import (
    HarnessContext,
    HarnessEvent,
    HarnessRequest,
    redact_secrets,
)

MODE_TO_SANDBOX = {
    "plan": "read-only",
    "read": "read-only",
    "edit": "workspace-write",
}
GPT2GIGA_ATTACHMENT_IDS_HEADER = "x-gpt2giga-attachment-ids"


def _write_codex_config(
    codex_home: Path,
    request: HarnessRequest,
    context: HarnessContext,
    *,
    attachment_file_ids: tuple[str, ...] = (),
) -> None:
    model = request.model or context.default_model or "GigaChat"
    options = request.extra.get("agent_adapter_options")
    reasoning_effort = (
        str(options.get("reasoning_effort"))
        if isinstance(options, Mapping)
        and options.get("reasoning_effort") in {"none", "low", "medium", "high"}
        else "none"
    )
    base_url = context.api_base_url(request.api_mode)
    custom_instructions = request.extra.get("developer_instructions")
    developer_instructions = codex_developer_instructions(
        custom_instructions if isinstance(custom_instructions, str) else ""
    )
    attachment_headers = ""
    if attachment_file_ids:
        header_value = ",".join(attachment_file_ids)
        attachment_headers = (
            'http_headers = { "'
            f'{GPT2GIGA_ATTACHMENT_IDS_HEADER}" = "{_toml_escape(header_value)}"'
            " }\n"
        )
    config = (
        f'model = "{_toml_escape(model)}"\n'
        'model_provider = "gigaloom"\n'
        f'model_reasoning_effort = "{reasoning_effort}"\n\n'
        f"developer_instructions = {_toml_string(developer_instructions)}\n\n"
        "[model_providers.gigaloom]\n"
        'name = "gigaloom"\n'
        f'base_url = "{_toml_escape(base_url)}"\n'
        'env_key = "GPT2GIGA_API_KEY"\n'
        'wire_api = "responses"\n'
        f"{attachment_headers}"
        "supports_websockets = false\n"
    )
    write_startup_config("codex-cli", codex_home, config)


def _upload_gigachat_attachments(
    request: HarnessRequest,
    context: HarnessContext,
) -> tuple[tuple[str, ...], tuple[HarnessEvent, ...], str | None]:
    """Upload planned document attachments and return provider file ids."""
    planned_ids = _planned_gigachat_upload_ids(request)
    if not planned_ids:
        return (), (), None
    attachments = {
        str(attachment.get("id") or ""): attachment
        for attachment in request.attachments
        if isinstance(attachment, Mapping)
    }
    file_ids: list[str] = []
    events: list[HarnessEvent] = []
    for attachment_id in planned_ids:
        attachment = attachments.get(attachment_id)
        if attachment is None:
            return (
                tuple(file_ids),
                tuple(events),
                "Planned GigaChat attachment is missing from the Harness request.",
            )
        source = str(attachment.get("storage_path") or "").strip()
        filename = str(attachment.get("filename") or "attachment")
        if not source:
            return (
                tuple(file_ids),
                tuple(events),
                f"{filename} has no stored payload for GigaChat Files upload.",
            )
        try:
            content = Path(source).expanduser().resolve().read_bytes()
            response = proxy.upload_file(
                context.proxy_url,
                request.api_mode,
                filename=filename,
                content=content,
                api_key=context.api_key
                or proxy.cached_sidecar_api_key(context.proxy_url),
                timeout=context.timeout_seconds,
            )
        except (OSError, proxy.ProxyRequestError) as exc:
            return (
                tuple(file_ids),
                tuple(events),
                str(redact_secrets(f"Failed to upload {filename}: {exc}")),
            )
        file_id = str(response.get("id") or "").strip()
        if not _valid_attachment_file_id(file_id):
            return (
                tuple(file_ids),
                tuple(events),
                f"GigaChat Files returned an invalid file id for {filename}.",
            )
        file_ids.append(file_id)
        events.append(
            HarnessEvent(
                type="attachment_uploaded",
                message=f"Uploaded {filename} to GigaChat Files.",
                payload={
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "file_id": file_id,
                    "transport": "gigachat_file_upload",
                },
            )
        )
    return tuple(file_ids), tuple(events), None


def _planned_gigachat_upload_ids(request: HarnessRequest) -> tuple[str, ...]:
    plan = request_render_plan(request)
    metadata_value = plan.get("metadata")
    if not isinstance(metadata_value, Mapping):
        return ()
    deliveries = metadata_value.get("deliveries")
    if not isinstance(deliveries, list | tuple):
        return ()
    result: list[str] = []
    for delivery in deliveries:
        if (
            isinstance(delivery, Mapping)
            and delivery.get("transport") == "gigachat_file_upload"
        ):
            attachment_id = str(delivery.get("attachment_id") or "").strip()
            if attachment_id and attachment_id not in result:
                result.append(attachment_id)
    return tuple(result)


def _headless_only_attachment_transports(
    request: HarnessRequest,
) -> tuple[str, ...]:
    """Return transports requiring one-shot Codex instead of app-server."""
    plan = request_render_plan(request)
    metadata_value = plan.get("metadata")
    if not isinstance(metadata_value, Mapping):
        return ()
    deliveries = metadata_value.get("deliveries")
    if not isinstance(deliveries, list | tuple):
        return ()
    result: list[str] = []
    for delivery in deliveries:
        if not isinstance(delivery, Mapping):
            continue
        surfaces_value = delivery.get("surfaces")
        if not isinstance(surfaces_value, list | tuple):
            continue
        surfaces = {
            str(surface)
            for surface in surfaces_value
            if isinstance(surface, str) and surface
        }
        if "headless_one_shot" not in surfaces or "structured_thread" in surfaces:
            continue
        transport = str(delivery.get("transport") or "").strip()
        if transport and transport not in result:
            result.append(transport)
    return tuple(result)


def _valid_attachment_file_id(value: str) -> bool:
    return (
        bool(value)
        and len(value) <= 200
        and all(character.isalnum() or character in "._:-" for character in value)
    )


def _managed_mcp_reference(request: HarnessRequest) -> Mapping[str, Any] | None:
    value = request.extra.get("managed_mcp_snapshot")
    return dict(value) if isinstance(value, Mapping) else None


def _structured_chat_prompt(request: HarnessRequest) -> str:
    """Include normalized Harness chat history in each ephemeral Codex turn."""
    prompt = prompt_with_attachments(request)
    history = tuple(request.messages[:-1]) if request.messages else ()
    if not history:
        return prompt
    transcript = "\n\n".join(
        f"[{message.role.upper()}]\n{message.content}" for message in history
    )
    return (
        "Continue the conversation below. Treat the final CURRENT USER REQUEST "
        "as the task to answer now.\n\n"
        f"CONVERSATION HISTORY\n{transcript}\n\n"
        f"CURRENT USER REQUEST\n{prompt}"
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)
