"""Durable worker command metadata and handlers."""

from __future__ import annotations

import argparse


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    worker = subparsers.add_parser("worker", parents=[common])
    worker_subparsers = worker.add_subparsers(dest="worker_command")

    worker_start = worker_subparsers.add_parser("start", parents=[common])
    worker_start.add_argument("--once", action="store_true")
    worker_start.add_argument("--poll-seconds", type=float, default=0.25)
    worker_start.add_argument("--max-idle-seconds", type=float, default=1.0)
    worker_start.add_argument("--lease-seconds", type=float, default=15.0)
    worker_start.add_argument("--heartbeat-seconds", type=float, default=2.0)
    worker_start.set_defaults(handler="_handle_worker_start")

    worker_status_parser = worker_subparsers.add_parser("status")
    worker_status_parser.add_argument("--json", action="store_true")
    worker_status_parser.set_defaults(handler="_handle_worker_status")

    worker_idle = worker_subparsers.add_parser("stop-on-idle", parents=[common])
    worker_idle.add_argument("--idle-seconds", type=float, default=5.0)
    worker_idle.add_argument("--poll-seconds", type=float, default=0.25)
    worker_idle.add_argument("--max-idle-seconds", type=float, default=1.0)
    worker_idle.add_argument("--lease-seconds", type=float, default=15.0)
    worker_idle.add_argument("--heartbeat-seconds", type=float, default=2.0)
    worker_idle.set_defaults(handler="_handle_worker_stop_on_idle")
