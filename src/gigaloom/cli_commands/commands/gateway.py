"""Route-local gateway diagnostics and lifecycle command metadata."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register gateway operator commands for final root composition."""
    gateway = subparsers.add_parser("gateway")
    commands = gateway.add_subparsers(dest="gateway_command")

    list_command = commands.add_parser("list")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(handler="_handle_gateway_list")

    inspect = commands.add_parser("inspect")
    inspect.add_argument("gateway_id")
    inspect.add_argument("--refresh", action="store_true")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(handler="_handle_gateway_inspect")

    doctor = commands.add_parser("doctor")
    doctor.add_argument("gateway_id")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(handler="_handle_gateway_doctor")

    start = commands.add_parser("start")
    start.add_argument("gateway_id")
    start.add_argument("--json", action="store_true")
    start.set_defaults(handler="_handle_gateway_start")

    stop = commands.add_parser("stop")
    stop.add_argument("gateway_id")
    stop.add_argument("--json", action="store_true")
    stop.set_defaults(handler="_handle_gateway_stop")


__all__ = ["register"]
