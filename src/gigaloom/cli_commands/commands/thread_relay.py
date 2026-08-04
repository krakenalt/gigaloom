"""Route-local Thread Relay command metadata for integrator composition."""

from __future__ import annotations

import argparse

from gigaloom.sessions.api import (
    ThreadAuthorMode,
    ThreadDeliveryIntent,
    ThreadSourceKind,
)


def register(
    session_subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    """Add bounded thread actions to an existing ``session`` command group."""
    threads = session_subparsers.add_parser("threads", parents=[common])
    _source(threads)
    _project(threads)
    threads.add_argument("--cursor", default=None)
    threads.add_argument("--limit", type=int, default=50)
    threads.add_argument("--json", action="store_true")
    threads.set_defaults(handler="_handle_thread_list")

    read = session_subparsers.add_parser("read", parents=[common])
    read.add_argument("thread_id")
    _source(read)
    _project(read)
    read.add_argument("--cursor", default=None)
    read.add_argument("--limit", type=int, default=50)
    read.add_argument("--json", action="store_true")
    read.set_defaults(handler="_handle_thread_read")

    send = session_subparsers.add_parser("send", parents=[common])
    send.add_argument("thread_id")
    _source(send)
    _project(send)
    send.add_argument("--source-thread-id", default=None)
    send.add_argument("--text", required=True)
    send.add_argument(
        "--intent",
        choices=tuple(item.value for item in ThreadDeliveryIntent),
        default=ThreadDeliveryIntent.MESSAGE.value,
    )
    send.add_argument(
        "--author-mode",
        choices=tuple(item.value for item in ThreadAuthorMode),
        default=ThreadAuthorMode.USER_AUTHORED.value,
    )
    send.add_argument("--expected-revision", default=None)
    send.add_argument("--active-turn", default=None)
    send.add_argument("--idempotency-key", default=None)
    send.add_argument("--expires-at", default=None)
    send.add_argument("--attachment", action="append", default=[])
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--json", action="store_true")
    send.set_defaults(handler="_handle_thread_send")

    status = session_subparsers.add_parser("status", parents=[common])
    status.add_argument("delivery_id")
    _project(status)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler="_handle_thread_status")


def _source(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--source",
        choices=tuple(item.value for item in ThreadSourceKind),
        default=ThreadSourceKind.GIGALOOM.value,
    )


def _project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-id",
        default=None,
        help="Exact project binding (defaults to the cataloged current workspace)",
    )


__all__ = ["register"]
