"""CLI presentation for bounded Thread Relay route-local actions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from gigaloom.config import HarnessConfig
from gigaloom.execution.thread_relay import (
    LOCAL_THREAD_ACTOR_SCOPE,
    ThreadRelayRouteActions,
    validated_preview,
)

if TYPE_CHECKING:
    from gigaloom.application import SessionApplicationService
    from gigaloom.sessions import FilesystemHarnessSessionStore


class ThreadRelayCommandHandlers:
    """Handlers installed with one actor/project-bound action service."""

    def __init__(self, actions: ThreadRelayRouteActions) -> None:
        self.actions = actions

    def list(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.actions.list_threads(
            source=args.source,
            cursor=args.cursor,
            limit=_limit(args.limit),
        )
        _print("Thread page", payload, json_output=args.json)
        return 0

    def read(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.actions.read_thread(
            source=args.source,
            thread_id=args.thread_id,
            cursor=args.cursor,
            limit=_limit(args.limit),
        )
        _print("Thread", payload, json_output=args.json)
        return 0

    def send(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        request = _send_request(args)
        preview = validated_preview(self.actions.preview_send(request))
        if args.dry_run:
            _print(
                "Thread delivery preview",
                {"dry_run": True, "preview": preview},
                json_output=args.json,
            )
            return 0
        delivery = self.actions.send(
            request,
            preview_digest=str(preview["preview_digest"]),
        )
        _print(
            "Thread delivery",
            {"dry_run": False, "preview": preview, "delivery": dict(delivery)},
            json_output=args.json,
        )
        return 0

    def status(self, args: argparse.Namespace, _config: HarnessConfig) -> int:
        payload = self.actions.status(args.delivery_id)
        _print("Thread delivery status", payload, json_output=args.json)
        return 0


def _send_request(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "source": args.source,
        "source_thread_id": args.source_thread_id,
        "thread_id": args.thread_id,
        "text": args.text,
        "intent": args.intent,
        "author_mode": args.author_mode,
        "expected_target_revision": args.expected_revision,
        "expected_active_turn_id": args.active_turn,
        "idempotency_key": args.idempotency_key,
        "expires_at": args.expires_at,
        "attachment_refs": list(args.attachment),
    }


def _actions(
    args: argparse.Namespace,
    config: HarnessConfig,
) -> ThreadRelayRouteActions:
    from gigaloom.execution.thread_relay import build_thread_relay_actions
    from gigaloom.sessions import FilesystemHarnessSessionStore

    session_store = FilesystemHarnessSessionStore(config.data_dir)
    return build_thread_relay_actions(
        actor_scope=LOCAL_THREAD_ACTOR_SCOPE,
        project_id=_project_id(args.project_id, config),
        session_store=session_store,
        data_dir=config.data_dir,
        turn_submitter=_SessionTurnSubmitter(config, session_store),
    )


class _SessionTurnSubmitter:
    """Lazily construct the existing durable submit owner only for send."""

    def __init__(
        self,
        config: HarnessConfig,
        session_store: FilesystemHarnessSessionStore,
    ) -> None:
        self.config = config
        self.session_store = session_store
        self.service: SessionApplicationService | None = None

    def submit_turn(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str,
        origin: str = "interactive",
    ) -> object:
        if self.service is None:
            from gigaloom.application import SessionApplicationService
            from gigaloom.registry import create_default_registry
            from gigaloom.runtime.payloads import DurableJobPayloadStore
            from gigaloom.runtime.store import RuntimeCoordinationStore
            from gigaloom.runtime.worker import DurableJobDispatcher
            from gigaloom.session_runner import HarnessSessionRunner
            from gigaloom.settings import HarnessSettingsStore

            runner = HarnessSessionRunner(
                registry=create_default_registry(),
                config=self.config,
                store=self.session_store,
            )
            runtime_store = RuntimeCoordinationStore(self.config.data_dir)
            self.service = SessionApplicationService(
                runner=runner,
                settings_store=HarnessSettingsStore(
                    self.config.data_dir,
                    self.config,
                ),
                runtime_store=runtime_store,
                dispatcher=DurableJobDispatcher(
                    runtime_store=runtime_store,
                    payload_store=DurableJobPayloadStore(self.config.data_dir),
                    runner=runner,
                ),
            )
        return self.service.submit_turn(
            session_id,
            payload,
            idempotency_key=idempotency_key,
            origin=origin,
        )


def _project_id(explicit: str | None, config: HarnessConfig) -> str:
    if explicit is not None and explicit.strip():
        return explicit.strip()

    from gigaloom.projects.api import FilesystemProjectCatalogRepository

    current = Path.cwd().resolve()
    repository = FilesystemProjectCatalogRepository(
        Path(config.data_dir) / "projects" / "catalog"
    )
    candidates: list[tuple[int, str]] = []
    cursor: str | None = None
    while True:
        page = repository.list_page(cursor=cursor, limit=100)
        for entry in page.items:
            canonical = entry.location.canonical_path
            if entry.state != "active" or canonical is None:
                continue
            root = Path(canonical)
            if current == root or current.is_relative_to(root):
                candidates.append((len(root.parts), entry.catalog_project_id))
        if not page.has_more or page.next_cursor is None:
            break
        cursor = page.next_cursor
    if not candidates:
        raise ValueError(
            "thread relay requires --project-id outside a cataloged project"
        )
    return max(candidates)[1]


def _handle_thread_list(args: argparse.Namespace, config: HarnessConfig) -> int:
    return ThreadRelayCommandHandlers(_actions(args, config)).list(args, config)


def _handle_thread_read(args: argparse.Namespace, config: HarnessConfig) -> int:
    return ThreadRelayCommandHandlers(_actions(args, config)).read(args, config)


def _handle_thread_send(args: argparse.Namespace, config: HarnessConfig) -> int:
    return ThreadRelayCommandHandlers(_actions(args, config)).send(args, config)


def _handle_thread_status(args: argparse.Namespace, config: HarnessConfig) -> int:
    return ThreadRelayCommandHandlers(_actions(args, config)).status(args, config)


def _limit(value: int) -> int:
    if isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("thread relay limit must be between 1 and 100")
    return value


def _print(
    label: str,
    payload: Mapping[str, Any],
    *,
    json_output: bool,
) -> None:
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    print(serialized if json_output else f"{label}: {serialized}")


__all__ = [
    "ThreadRelayCommandHandlers",
    "_handle_thread_list",
    "_handle_thread_read",
    "_handle_thread_send",
    "_handle_thread_status",
]
