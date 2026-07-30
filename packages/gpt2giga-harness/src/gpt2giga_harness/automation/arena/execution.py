"""Execution for the arena subcontext."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any, Mapping
from gpt2giga_harness.execution import ExecutionTransport
from gpt2giga_harness.automation.ports import admitted_durable_structured_capabilities
from gpt2giga_harness.automation.ports import DurableJobDispatcher
from gpt2giga_harness.automation.ports import redact_for_storage
from gpt2giga_harness.automation.ports import title_from_prompt, utc_now
from gpt2giga_harness.session_runner import HarnessSessionRunner
from gpt2giga_harness.automation.ports import HarnessRun
from gpt2giga_harness.automation.ports import SessionNotFoundError
from .codec import (
    _optional_text as _optional_text,
    _text_tuple as _text_tuple,
    arena_request_from_payload as arena_request_from_payload,
)
from .models import (
    HarnessArenaChildRun as HarnessArenaChildRun,
    HarnessArenaRequest as HarnessArenaRequest,
    HarnessArenaRun as HarnessArenaRun,
)
from .store import FilesystemHarnessArenaStore as FilesystemHarnessArenaStore


def run_arena(
    *,
    runner: HarnessSessionRunner,
    arena_store: FilesystemHarnessArenaStore,
    payload: Mapping[str, Any],
    session_id: str | None = None,
) -> HarnessArenaRun:
    """Run a multi-harness comparison with isolated concurrent children."""
    request = arena_request_from_payload(payload)
    if request.execution_transport is ExecutionTransport.NATIVE_STRUCTURED:
        raise ValueError("native_structured Arena requires the durable runtime")
    session = (
        runner.store.get_session(session_id)
        if session_id is not None
        else runner.create_session(
            title=title_from_prompt(request.prompt),
            workspace=request.workspace,
            default_harness_id=request.harness_ids[0],
            default_model=request.model,
            default_api_mode=request.api_mode,
            default_mode=request.mode,
        )
    )
    arena = arena_store.create(request, session_id=session.id)
    children = _create_arena_child_sessions(runner, arena, request)
    for child in children:
        arena_store.upsert_child(arena.id, child)
    with ThreadPoolExecutor(
        max_workers=min(len(children), 4),
        thread_name_prefix=f"{arena.id}-child",
    ) as executor:
        futures = {
            executor.submit(
                _run_arena_child,
                runner=runner,
                session_id=child.session_id or "",
                arena=arena,
                request=request,
                harness_id=child.harness_id,
                index=child.index,
                turn_index=0,
            ): child.index
            for child in children
        }
        for future in as_completed(futures):
            arena_store.upsert_child(arena.id, future.result())
    return arena_store.get(arena.id)


def queue_arena(
    *,
    runner: HarnessSessionRunner,
    dispatcher: DurableJobDispatcher,
    arena_store: FilesystemHarnessArenaStore,
    payload: Mapping[str, Any],
    session_id: str | None = None,
) -> HarnessArenaRun:
    """Queue arena children as independent durable jobs."""
    request = arena_request_from_payload(payload)
    if request.execution_transport is ExecutionTransport.NATIVE_STRUCTURED:
        for harness_id in request.harness_ids:
            admitted_durable_structured_capabilities(runner.registry.get(harness_id))
    session = (
        runner.store.get_session(session_id)
        if session_id is not None
        else runner.create_session(
            title=title_from_prompt(request.prompt),
            workspace=request.workspace,
            default_harness_id=request.harness_ids[0],
            default_model=request.model,
            default_api_mode=request.api_mode,
            default_mode=request.mode,
        )
    )
    arena = arena_store.create(request, session_id=session.id)
    children = _create_arena_child_sessions(runner, arena, request)
    for child in children:
        child_payload = _arena_child_payload(
            arena=arena,
            request=request,
            harness_id=child.harness_id,
            index=child.index,
            turn_index=0,
        )
        submission = dispatcher.submit(
            child.session_id or "",
            child_payload,
            idempotency_key=f"arena:{arena.id}:{child.index}:turn:0",
            origin="manual",
        )
        arena = arena_store.upsert_child(
            arena.id,
            HarnessArenaChildRun(
                harness_id=child.harness_id,
                index=child.index,
                session_id=child.session_id,
                run_id=submission.queued.run.id,
                status="queued",
            ),
        )
    return arena


def continue_arena(
    *,
    runner: HarnessSessionRunner,
    arena_store: FilesystemHarnessArenaStore,
    arena: HarnessArenaRun,
    payload: Mapping[str, Any],
) -> HarnessArenaRun:
    """Fan one shared follow-up out to every isolated child concurrently."""
    if arena.execution_transport is ExecutionTransport.NATIVE_STRUCTURED:
        raise ValueError("native_structured Arena requires the durable runtime")
    request, turn_index = _follow_up_request(arena_store, arena, payload)
    children = _require_arena_children(arena_store.get(arena.id))
    for child in children:
        arena_store.upsert_child(arena.id, replace(child, status="running", error=None))
    with ThreadPoolExecutor(
        max_workers=min(len(children), 4),
        thread_name_prefix=f"{arena.id}-follow-up",
    ) as executor:
        futures = [
            executor.submit(
                _run_arena_child,
                runner=runner,
                session_id=child.session_id or "",
                arena=arena,
                request=request,
                harness_id=child.harness_id,
                index=child.index,
                turn_index=turn_index,
            )
            for child in children
        ]
        for future in as_completed(futures):
            arena_store.upsert_child(arena.id, future.result())
    return arena_store.get(arena.id)


def queue_arena_follow_up(
    *,
    runner: HarnessSessionRunner,
    dispatcher: DurableJobDispatcher,
    arena_store: FilesystemHarnessArenaStore,
    arena: HarnessArenaRun,
    payload: Mapping[str, Any],
) -> HarnessArenaRun:
    """Queue one shared follow-up as independent durable child jobs."""
    request, turn_index = _follow_up_request(arena_store, arena, payload)
    for child in _require_arena_children(arena_store.get(arena.id)):
        child_payload = _arena_child_payload(
            arena=arena,
            request=request,
            harness_id=child.harness_id,
            index=child.index,
            turn_index=turn_index,
        )
        submission = dispatcher.submit(
            child.session_id or "",
            child_payload,
            idempotency_key=f"arena:{arena.id}:{child.index}:turn:{turn_index}",
            origin="manual",
        )
        arena_store.upsert_child(
            arena.id,
            HarnessArenaChildRun(
                harness_id=child.harness_id,
                index=child.index,
                session_id=child.session_id,
                run_id=submission.queued.run.id,
                status="queued",
            ),
        )
    return arena_store.get(arena.id)


def sync_durable_arena_child(
    data_dir: str,
    payload: Mapping[str, Any],
    run: HarnessRun,
    result_text: str,
) -> None:
    """Project one finished durable run into its arena parent record."""
    extra = payload.get("extra")
    arena_meta = extra.get("arena") if isinstance(extra, Mapping) else None
    if not isinstance(arena_meta, Mapping) or not arena_meta.get("arena_id"):
        return
    FilesystemHarnessArenaStore(data_dir).upsert_child(
        str(arena_meta["arena_id"]),
        _child_from_run(
            run.harness_id,
            int(arena_meta.get("child_index") or 0),
            run,
            result_text,
        ),
    )


def _run_arena_child(
    *,
    runner: HarnessSessionRunner,
    session_id: str,
    arena: HarnessArenaRun,
    request: HarnessArenaRequest,
    harness_id: str,
    index: int,
    turn_index: int,
) -> HarnessArenaChildRun:
    child_payload = _arena_child_payload(
        arena=arena,
        request=request,
        harness_id=harness_id,
        index=index,
        turn_index=turn_index,
    )
    try:
        result = runner.run_in_session(session_id, child_payload)
    except SessionNotFoundError:
        raise
    except Exception as exc:
        return HarnessArenaChildRun(
            harness_id=harness_id,
            index=index,
            session_id=session_id,
            run_id=None,
            status="failed",
            error=str(redact_for_storage(str(exc))),
        )
    return _child_from_run(harness_id, index, result.run, result.result.text)


def _arena_child_payload(
    *,
    arena: HarnessArenaRun,
    request: HarnessArenaRequest,
    harness_id: str,
    index: int,
    turn_index: int,
) -> dict[str, Any]:
    return {
        "harness_id": harness_id,
        "prompt": request.prompt,
        "model": request.model,
        "api_mode": request.api_mode.value,
        "mode": request.mode,
        "workspace": request.workspace,
        "workspace_policy": request.workspace_policy,
        "attachment_ids": list(request.attachment_ids),
        "invocation_mode": (
            "native"
            if request.execution_transport is ExecutionTransport.NATIVE_STRUCTURED
            else "headless"
        ),
        "execution_transport": (
            request.execution_transport.value
            if request.execution_transport is not None
            else None
        ),
        "extra": {
            **dict(request.extra),
            "arena": {
                "arena_id": arena.id,
                "child_index": index,
                "child_count": len(request.harness_ids),
                "parent_session_id": arena.session_id,
                "turn_index": turn_index,
            },
        },
    }


def _create_arena_child_sessions(
    runner: HarnessSessionRunner,
    arena: HarnessArenaRun,
    request: HarnessArenaRequest,
) -> tuple[HarnessArenaChildRun, ...]:
    children: list[HarnessArenaChildRun] = []
    for index, harness_id in enumerate(request.harness_ids):
        session = runner.create_session(
            title=f"{title_from_prompt(request.prompt)} · {harness_id}",
            workspace=request.workspace,
            default_harness_id=harness_id,
            default_model=request.model,
            default_api_mode=request.api_mode,
            default_mode=request.mode,
        )
        runner.store.update_session(
            session.id,
            metadata={
                **dict(session.metadata),
                "arena_id": arena.id,
                "arena_parent_session_id": arena.session_id,
                "arena_child_index": index,
            },
        )
        children.append(
            HarnessArenaChildRun(
                harness_id=harness_id,
                index=index,
                session_id=session.id,
                run_id=None,
                status="queued",
            )
        )
    return tuple(children)


def _follow_up_request(
    arena_store: FilesystemHarnessArenaStore,
    arena: HarnessArenaRun,
    payload: Mapping[str, Any],
) -> tuple[HarnessArenaRequest, int]:
    prompt = str(payload.get("prompt") or "")
    if not prompt.strip():
        raise ValueError("prompt is required")
    attachment_ids = _text_tuple(payload.get("attachment_ids"), "attachment_ids")
    model = _optional_text(payload.get("model")) if "model" in payload else arena.model
    turn_index = max(int(arena.metadata.get("turn_count") or 0), 0) + 1
    updated = replace(
        arena,
        model=model,
        updated_at=utc_now(),
        metadata={**dict(arena.metadata), "turn_count": turn_index},
    )
    arena_store.save(updated)
    return (
        HarnessArenaRequest(
            prompt=prompt,
            harness_ids=arena.harness_ids,
            model=model,
            api_mode=arena.api_mode,
            mode=arena.mode,
            workspace=arena.workspace,
            attachment_ids=attachment_ids,
            workspace_policy=arena.workspace_policy,
            execution_transport=arena.execution_transport,
            extra={},
        ),
        turn_index,
    )


def _require_arena_children(
    arena: HarnessArenaRun,
) -> tuple[HarnessArenaChildRun, ...]:
    children = tuple(
        child for child in arena.child_runs if child.session_id is not None
    )
    if len(children) != len(arena.harness_ids):
        raise ValueError("arena child sessions are incomplete")
    return children


def _child_from_run(
    harness_id: str,
    index: int,
    run: HarnessRun,
    result_text: str,
) -> HarnessArenaChildRun:
    return HarnessArenaChildRun(
        harness_id=harness_id,
        index=index,
        session_id=run.session_id,
        run_id=run.id,
        status=run.status,
        error=run.error,
        result_text=(
            str(redact_for_storage(result_text)) if run.status == "succeeded" else None
        ),
    )
