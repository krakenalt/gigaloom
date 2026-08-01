"""Managed ACP Registry agent lifecycle command metadata."""

from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the unified agent family plus its exact ACP alias handlers."""
    agent = subparsers.add_parser("agent")
    _register_agent_subcommands(agent.add_subparsers(dest="agent_command"))
    acp = subparsers.add_parser("acp")
    _register_acp_aliases(acp.add_subparsers(dest="acp_command"))


def _register_agent_subcommands(subparsers: argparse._SubParsersAction) -> None:
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--refresh", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler="_handle_agent_runtime_search")

    add = subparsers.add_parser("add")
    add.add_argument("registry_query", nargs="?")
    add.add_argument("--manifest")
    add.add_argument("--as", dest="local_agent_id")
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--yes", action="store_true")
    add.add_argument("--allow-unverified", action="store_true")
    add.add_argument("--refresh", action="store_true")
    add.add_argument("--json", action="store_true")
    add.set_defaults(handler="_handle_agent_runtime_add")

    list_command = subparsers.add_parser("list")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(handler="_handle_agent_runtime_list")

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("local_agent_id")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(handler="_handle_agent_runtime_inspect")

    probe = subparsers.add_parser("probe")
    probe.add_argument("local_agent_id")
    probe.add_argument("--route", default=None)
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(handler="_handle_agent_runtime_probe")

    outdated = subparsers.add_parser("outdated")
    outdated.add_argument("--refresh", action="store_true")
    outdated.add_argument("--json", action="store_true")
    outdated.set_defaults(handler="_handle_agent_runtime_outdated")

    update = subparsers.add_parser("update")
    update.add_argument("local_agent_id")
    update.add_argument("--yes", action="store_true")
    update.add_argument("--allow-unverified", action="store_true")
    update.add_argument("--refresh", action="store_true")
    update.add_argument("--json", action="store_true")
    update.set_defaults(handler="_handle_agent_runtime_update")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("local_agent_id")
    rollback.add_argument("--yes", action="store_true")
    rollback.add_argument("--json", action="store_true")
    rollback.set_defaults(handler="_handle_agent_runtime_rollback")

    remove = subparsers.add_parser("remove")
    remove.add_argument("local_agent_id")
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--yes", action="store_true")
    remove.add_argument("--json", action="store_true")
    remove.set_defaults(handler="_handle_agent_runtime_remove")

    lock = subparsers.add_parser("lock")
    lock.add_argument("--output", required=True)
    lock.add_argument("--json", action="store_true")
    lock.set_defaults(handler="_handle_agent_runtime_lock")

    sync = subparsers.add_parser("sync")
    sync.add_argument("--lock", required=True)
    sync.add_argument("--yes", action="store_true")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(handler="_handle_agent_runtime_sync")

    discover = subparsers.add_parser("discover")
    discover.add_argument("--registry", required=True)
    discover.add_argument("--dry-run", action="store_true")
    discover.add_argument("--json", action="store_true")
    discover.set_defaults(handler="_handle_agent_profile_discover")

    upgrade = subparsers.add_parser("upgrade")
    upgrade_subparsers = upgrade.add_subparsers(dest="agent_upgrade_command")
    check = upgrade_subparsers.add_parser("check")
    check.add_argument("agent_id")
    check.add_argument("--candidate-command", required=True)
    check.add_argument("--corpus", required=True)
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler="_handle_agent_upgrade_check")


def _register_acp_aliases(subparsers: argparse._SubParsersAction) -> None:
    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--refresh", action="store_true")
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler="_handle_agent_runtime_search")

    add = subparsers.add_parser("add")
    add.add_argument("registry_query")
    add.add_argument("--as", dest="local_agent_id")
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--yes", action="store_true")
    add.add_argument("--allow-unverified", action="store_true")
    add.add_argument("--refresh", action="store_true")
    add.add_argument("--json", action="store_true")
    add.set_defaults(manifest=None, handler="_handle_agent_runtime_add")
