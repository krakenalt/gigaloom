"""Run execution helpers shared by UI domain routers."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Mapping

from gigaloom.sessions import (
    HarnessSessionStore,
)
from gigaloom.sessions.models import (
    HarnessMessage,
    HarnessRun,
    HarnessSession,
)
from gigaloom.sessions.store import new_id, utc_now
from gigaloom.ui.async_execution import (
    run_in_threadpool,
)
from gigaloom.ui.services.session_queries import recent_runs


def _fork_session_from_run(
    store: HarnessSessionStore, run: HarnessRun
) -> HarnessSession:
    source = store.get_session(run.session_id)
    run_thread = run.metadata.get("app_server_thread")
    session_thread = source.metadata.get("app_server_thread")
    source_thread = (
        dict(run_thread)
        if isinstance(run_thread, Mapping)
        else dict(session_thread)
        if isinstance(session_thread, Mapping)
        else {}
    )
    metadata = {
        **dict(source.metadata),
        "forked_from_session_id": source.id,
        "forked_from_run_id": run.id,
    }
    metadata.pop("app_server_thread", None)
    metadata.pop("structured_session_link", None)
    metadata.pop("app_server_fork", None)
    if source_thread.get("thread_id"):
        metadata["app_server_fork"] = {
            "thread_id": source_thread["thread_id"],
            "turn_id": source_thread.get("latest_turn_id"),
            "source_session_id": source.id,
            "source_run_id": run.id,
        }
    fork = store.create_session(
        title=f"Fork: {source.title}",
        workspace=run.workspace or source.workspace,
        default_harness_id=run.harness_id,
        default_model=run.model,
        default_api_mode=run.api_mode,
        default_mode=run.mode,
        metadata=metadata,
    )
    for message in _messages_through_run(store.list_messages(source.id), run.id):
        store.append_message(
            replace(
                message,
                id=new_id("msg"),
                session_id=fork.id,
                run_id=None,
                created_at=utc_now(),
                metadata={
                    **dict(message.metadata),
                    "forked_from_message_id": message.id,
                    "forked_from_run_id": run.id,
                },
            )
        )
    return fork


def _messages_through_run(
    messages: tuple[HarnessMessage, ...], run_id: str
) -> tuple[HarnessMessage, ...]:
    selected: list[HarnessMessage] = []
    seen_target_run = False
    for message in messages:
        selected.append(message)
        if message.run_id == run_id:
            seen_target_run = True
            if message.role in {"assistant", "error"}:
                break
        elif seen_target_run:
            selected.pop()
            break
    return tuple(selected)


async def _wait_for_started_run(
    *,
    store: HarnessSessionStore,
    session_id: str,
    before_run_ids: set[str],
    task: asyncio.Task[Any],
) -> HarnessRun:
    for _ in range(200):
        stored_runs = await run_in_threadpool(
            recent_runs,
            store,
            session_id,
            limit=100,
        )
        runs = [run for run in stored_runs if run.id not in before_run_ids]
        if runs:
            return runs[-1]
        if task.done():
            task.result()
            break
        await asyncio.sleep(0.01)
    raise RuntimeError("Harness run did not start")
