"""CLI presentation for bounded Thread Relay route-local actions."""

from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from gigaloom.config import HarnessConfig
from gigaloom.execution.thread_relay import (
    ThreadRelayRouteActions,
    validated_preview,
)


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


__all__ = ["ThreadRelayCommandHandlers"]
