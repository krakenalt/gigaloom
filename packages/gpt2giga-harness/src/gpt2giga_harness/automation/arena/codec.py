"""Codec for the arena subcontext."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.automation.ports import requested_execution_transport
from gpt2giga_harness.automation.ports import redact_for_storage
from gpt2giga_harness.types import parse_api_mode
from gpt2giga_harness.projects.api import resolve_workspace
from .models import (
    HarnessArenaChildRun as HarnessArenaChildRun,
    HarnessArenaRequest as HarnessArenaRequest,
    HarnessArenaRun as HarnessArenaRun,
)


def arena_request_from_payload(payload: Mapping[str, Any]) -> HarnessArenaRequest:
    """Parse an API payload into an arena request."""
    prompt = str(payload.get("prompt") or "")
    harness_ids = _harness_ids(payload.get("harness_ids"))
    if not prompt.strip():
        raise ValueError("prompt is required")
    if not harness_ids:
        raise ValueError("harness_ids must contain at least one harness")
    extra = payload.get("extra") if isinstance(payload.get("extra"), Mapping) else {}
    return HarnessArenaRequest(
        prompt=prompt,
        harness_ids=harness_ids,
        model=_optional_text(payload.get("model")),
        api_mode=parse_api_mode(payload.get("api_mode")),
        mode=str(payload.get("mode") or "plan"),
        workspace=resolve_workspace(_optional_text(payload.get("workspace"))),
        attachment_ids=_text_tuple(payload.get("attachment_ids"), "attachment_ids"),
        workspace_policy=_arena_workspace_policy(
            str(payload.get("workspace_policy") or "auto"),
            mode=str(payload.get("mode") or "plan"),
            execution_transport=requested_execution_transport(payload),
        ),
        execution_transport=requested_execution_transport(payload),
        extra=dict(extra),
    )


def arena_to_dict(arena: HarnessArenaRun) -> dict[str, Any]:
    """Serialize an arena run."""
    return {
        "id": arena.id,
        "session_id": arena.session_id,
        "status": arena.status,
        "prompt": arena.prompt,
        "harness_ids": list(arena.harness_ids),
        "model": arena.model,
        "api_mode": arena.api_mode.value,
        "mode": arena.mode,
        "workspace": arena.workspace,
        "attachment_ids": list(arena.attachment_ids),
        "workspace_policy": arena.workspace_policy,
        "execution_transport": (
            arena.execution_transport.value
            if arena.execution_transport is not None
            else None
        ),
        "created_at": arena.created_at,
        "updated_at": arena.updated_at,
        "child_runs": [arena_child_to_dict(child) for child in arena.child_runs],
        "metadata": dict(arena.metadata),
    }


def arena_from_dict(data: Mapping[str, Any]) -> HarnessArenaRun:
    """Parse a persisted arena run."""
    return HarnessArenaRun(
        id=str(data["id"]),
        session_id=str(data["session_id"]),
        status=str(data.get("status") or "running"),
        prompt=str(data.get("prompt") or ""),
        harness_ids=tuple(str(item) for item in data.get("harness_ids", ())),
        model=_optional_text(data.get("model")),
        api_mode=parse_api_mode(data.get("api_mode")),
        mode=str(data.get("mode") or "plan"),
        workspace=_optional_text(data.get("workspace")),
        attachment_ids=tuple(str(item) for item in data.get("attachment_ids", ())),
        workspace_policy=str(data.get("workspace_policy") or "auto"),
        execution_transport=requested_execution_transport(data),
        created_at=str(data["created_at"]),
        updated_at=str(data.get("updated_at") or data["created_at"]),
        child_runs=tuple(
            arena_child_from_dict(item) for item in data.get("child_runs", ())
        ),
        metadata=_mapping(data.get("metadata")),
    )


def arena_child_to_dict(child: HarnessArenaChildRun) -> dict[str, Any]:
    """Serialize one arena child run."""
    return {
        "harness_id": child.harness_id,
        "index": child.index,
        "session_id": child.session_id,
        "run_id": child.run_id,
        "status": child.status,
        "error": child.error,
        "result_text": child.result_text,
    }


def arena_child_from_dict(data: Mapping[str, Any]) -> HarnessArenaChildRun:
    """Parse one arena child run."""
    return HarnessArenaChildRun(
        harness_id=str(data["harness_id"]),
        index=int(data.get("index") or 0),
        session_id=_optional_text(data.get("session_id")),
        run_id=_optional_text(data.get("run_id")),
        status=str(data.get("status") or "queued"),
        error=_optional_text(data.get("error")),
        result_text=_optional_text(data.get("result_text")),
    )


def _harness_ids(value: Any) -> tuple[str, ...]:
    ids = _text_tuple(value, "harness_ids")
    seen: set[str] = set()
    unique: list[str] = []
    for harness_id in ids:
        if harness_id in seen:
            continue
        seen.add(harness_id)
        unique.append(harness_id)
    return tuple(unique)


def _text_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    values: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is None:
            raise ValueError(f"{field_name} must contain non-empty strings")
        values.append(text)
    return tuple(values)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _arena_workspace_policy(
    requested: str,
    *,
    mode: str,
    execution_transport: ExecutionTransport | None,
) -> str:
    if execution_transport is ExecutionTransport.NATIVE_STRUCTURED and mode == "edit":
        return "worktree"
    return requested


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _redacted_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    redacted = redact_for_storage(dict(value))
    if isinstance(redacted, Mapping):
        return dict(redacted)
    return {}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
