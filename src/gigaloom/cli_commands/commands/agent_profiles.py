"""Declarative Agent Profile command metadata."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the version-1 Agent Profile inventory command family."""
    agent = subparsers.add_parser("agent")
    agent_subparsers = agent.add_subparsers(dest="agent_command")

    list_command = agent_subparsers.add_parser("list")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(handler="_handle_agent_profile_list")

    inspect = agent_subparsers.add_parser("inspect")
    inspect.add_argument("agent_id")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(handler="_handle_agent_profile_inspect")

    add = agent_subparsers.add_parser("add")
    add.add_argument("--manifest", required=True)
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--json", action="store_true")
    add.set_defaults(handler="_handle_agent_profile_add")

    remove = agent_subparsers.add_parser("remove")
    remove.add_argument("agent_id")
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--json", action="store_true")
    remove.set_defaults(handler="_handle_agent_profile_remove")

    probe = agent_subparsers.add_parser("probe")
    probe.add_argument("agent_id")
    probe.add_argument("--route", default=None)
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(handler="_handle_agent_probe_plan")

    discover = agent_subparsers.add_parser("discover")
    discover.add_argument("--registry", required=True)
    discover.add_argument("--dry-run", action="store_true")
    discover.add_argument("--json", action="store_true")
    discover.set_defaults(handler="_handle_agent_profile_discover")
