"""Governed Route Advisor command metadata for final CLI composition."""

from __future__ import annotations

import argparse

from gigaloom.execution.api import RouteIntent


def register(
    subparsers: argparse._SubParsersAction,
    common: argparse.ArgumentParser,
) -> None:
    """Register recommendation, inspection, and manual override commands."""
    route = subparsers.add_parser("route")
    route_subparsers = route.add_subparsers(dest="route_command")

    recommend = route_subparsers.add_parser("recommend", parents=[common])
    recommend.add_argument("--project", required=True)
    recommend.add_argument(
        "--intent",
        choices=tuple(item.value for item in RouteIntent),
        required=True,
    )
    recommend.add_argument("--task-digest", required=True)
    recommend.add_argument("--context-manifest-digest", required=True)
    recommend.add_argument("--capability", action="append", default=[])
    recommend.add_argument("--transport", action="append", default=[])
    recommend.add_argument("--workspace-policy", default="read_only")
    recommend.add_argument("--network-policy", default="denied")
    recommend.add_argument("--cost-policy", default="explicit_unlimited")
    recommend.add_argument("--platform", default=None)
    recommend.add_argument("--launch-profile", default=None)
    recommend.add_argument("--prefer-route", default=None)
    recommend.add_argument("--host", default=None)
    recommend.add_argument("--account-digest", default=None)
    recommend.add_argument("--require-known-cost", action="store_true")
    recommend.add_argument("--require-sealed-evaluation", action="store_true")
    recommend.add_argument("--require-session-portability", action="store_true")
    recommend.add_argument("--json", action="store_true")
    recommend.set_defaults(handler="_handle_route_recommend")

    show = route_subparsers.add_parser("show", parents=[common])
    show.add_argument("route_decision_id")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler="_handle_route_show")

    override = route_subparsers.add_parser("override", parents=[common])
    override.add_argument("route_decision_id")
    override.add_argument("--route", dest="selected_route_id", required=True)
    override.add_argument("--reason", required=True)
    override.add_argument("--json", action="store_true")
    override.set_defaults(handler="_handle_route_override")


__all__ = ["register"]
