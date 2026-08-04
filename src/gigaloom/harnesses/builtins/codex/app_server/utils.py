"""Codex app-server bounded serialization and filesystem utilities."""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from gigaloom.managed_mcp import (
    write_startup_config,
)
from gigaloom.contracts.codex_instructions import codex_developer_instructions
from gigaloom.types import (
    HarnessContext,
    HarnessEvent,
    HarnessRequest,
    emit_event,
)

from gigaloom.harnesses.builtins.codex.app_server.contracts import (
    AppServerProtocolError,
)


def _thread_id(response: Mapping[str, Any]) -> str:
    thread = _mapping(response.get("thread"))
    thread_id = str(thread.get("id") or "").strip()
    if not thread_id:
        raise AppServerProtocolError("Codex app-server returned no thread id")
    return thread_id


def _turn_id(response: Mapping[str, Any]) -> str:
    turn = _mapping(response.get("turn"))
    turn_id = str(turn.get("id") or "").strip()
    if not turn_id:
        raise AppServerProtocolError("Codex app-server returned no turn id")
    return turn_id


def _validate_link_snapshot(
    link: Mapping[str, Any], snapshot: Mapping[str, Any]
) -> None:
    expected = str(link.get("snapshot_hash") or "")
    current = str(snapshot.get("snapshot_hash") or "")
    if expected != current:
        raise ValueError(
            "Codex app-server continuation changed route, model, workspace, "
            "permission mode, managed home, or tool snapshot; fork explicitly."
        )


def _public_link(link: Mapping[str, Any] | None) -> dict[str, Any]:
    if link is None:
        return {}
    return {
        key: value
        for key, value in link.items()
        if key
        not in {
            "last_prompt_id",
        }
    }


def _scope_id(command: tuple[str, ...], snapshot: Mapping[str, Any]) -> str:
    value = {
        "command": list(command),
        "cli_version": snapshot.get("cli_version"),
        "api_mode": snapshot.get("api_mode"),
        "managed_home_id": snapshot.get("managed_home_id"),
        "tool_snapshot_hash": snapshot.get("tool_snapshot_hash"),
        "personalization_revision": snapshot.get("personalization_revision"),
    }
    return _json_hash(value)[:24]


def _write_provider_config(
    home: Path,
    request: HarnessRequest,
    context: HarnessContext,
) -> None:
    model = request.model or context.default_model or "GigaChat"
    options = _mapping(request.extra.get("agent_adapter_options"))
    effort = str(options.get("reasoning_effort") or "none")
    if effort not in {"none", "low", "medium", "high"}:
        effort = "none"
    base_url = context.api_base_url(request.api_mode)
    custom_instructions = request.extra.get("developer_instructions")
    developer_instructions = codex_developer_instructions(
        custom_instructions if isinstance(custom_instructions, str) else ""
    )
    config = (
        f'model = "{_toml_escape(model)}"\n'
        'model_provider = "gigaloom"\n'
        f'model_reasoning_effort = "{effort}"\n\n'
        f"developer_instructions = {json.dumps(developer_instructions, ensure_ascii=False)}\n\n"
        "[model_providers.gigaloom]\n"
        'name = "gigaloom"\n'
        f'base_url = "{_toml_escape(base_url)}"\n'
        'env_key = "GPT2GIGA_API_KEY"\n'
        'wire_api = "responses"\n'
        "supports_websockets = false\n"
    )
    write_startup_config("codex-cli", home, config)


def _publish(
    request: HarnessRequest,
    collected: list[HarnessEvent],
    event: HarnessEvent,
) -> None:
    if not emit_event(request, event):
        collected.append(event)


def _cancel_requested(cancel_event: Any | None) -> bool:
    if cancel_event is None:
        return False
    is_set = getattr(cancel_event, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _structured_link_id(session_id: str) -> str:
    return f"codex-link-{hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:32]}"


def _adapter_version() -> str:
    try:
        value = metadata.version("gigaloom")
    except metadata.PackageNotFoundError:
        value = "unknown"
    return _driver_version(value)


def _driver_version(value: Any) -> str:
    return _identity_value(value or "unknown", prefix="version")


def _identity_value(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    allowed = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:@+~-"
    )
    if (
        text
        and len(text) <= 256
        and text[0].isalnum()
        and all(character in allowed for character in text)
    ):
        return text
    return f"{prefix}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _json_hash(value: Mapping[str, Any]) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
