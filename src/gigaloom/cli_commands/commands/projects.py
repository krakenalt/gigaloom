"""Project Catalog and Launch Profile command metadata.

The central CLI registry remains integration-owner territory.  Wave B2 exposes
this cohesive registrar so the integration slice can attach it to the existing
``giga project`` parser without duplicating command definitions.
"""

from __future__ import annotations

import argparse


def register_project_catalog_commands(
    project_subparsers: argparse._SubParsersAction,
) -> None:
    """Register bounded catalog, session-move, and profile workflows."""
    list_command = project_subparsers.add_parser("list")
    list_command.add_argument("--cursor", default=None)
    list_command.add_argument("--limit", type=int, default=50)
    list_command.add_argument("--include-tombstoned", action="store_true")
    list_command.add_argument("--json", action="store_true")
    list_command.set_defaults(handler="_handle_project_catalog_list")

    add = project_subparsers.add_parser("add")
    add.add_argument("path")
    add.add_argument("--name", required=True)
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--json", action="store_true")
    add.set_defaults(handler="_handle_project_catalog_add")

    rename = project_subparsers.add_parser("rename")
    rename.add_argument("catalog_project_id")
    rename.add_argument("new_name")
    _add_revision_argument(rename)
    rename.add_argument("--dry-run", action="store_true")
    rename.add_argument("--json", action="store_true")
    rename.set_defaults(handler="_handle_project_catalog_rename")

    relocate = project_subparsers.add_parser("relocate")
    relocate.add_argument("catalog_project_id")
    relocate.add_argument("new_path")
    _add_revision_argument(relocate)
    relocate.add_argument("--confirm-identity-change", action="store_true")
    relocate.add_argument("--dry-run", action="store_true")
    relocate.add_argument("--json", action="store_true")
    relocate.set_defaults(handler="_handle_project_catalog_relocate")

    remove = project_subparsers.add_parser("remove")
    remove.add_argument("catalog_project_id")
    _add_revision_argument(remove)
    remove.add_argument("--dry-run", action="store_true")
    remove.add_argument("--json", action="store_true")
    remove.set_defaults(handler="_handle_project_catalog_remove")

    move_session = project_subparsers.add_parser("move-session")
    move_session.add_argument("session_id")
    move_session.add_argument("--to", required=True, dest="target_project_id")
    move_session.add_argument("--expected-updated-at", default=None)
    move_session.add_argument("--dry-run", action="store_true")
    move_session.add_argument("--json", action="store_true")
    move_session.set_defaults(handler="_handle_project_catalog_move_session")

    profile = project_subparsers.add_parser("profile")
    profile_subparsers = profile.add_subparsers(dest="project_profile_command")

    profile_list = profile_subparsers.add_parser("list")
    profile_list.add_argument("catalog_project_id")
    profile_list.add_argument("--cursor", default=None)
    profile_list.add_argument("--limit", type=int, default=50)
    profile_list.add_argument("--json", action="store_true")
    profile_list.set_defaults(handler="_handle_project_profile_list")

    profile_create = profile_subparsers.add_parser("create")
    profile_create.add_argument("catalog_project_id")
    profile_create.add_argument("--name", required=True)
    _add_profile_create_arguments(profile_create)
    profile_create.add_argument("--dry-run", action="store_true")
    profile_create.add_argument("--json", action="store_true")
    profile_create.set_defaults(handler="_handle_project_profile_create")

    profile_update = profile_subparsers.add_parser("update")
    profile_update.add_argument("launch_profile_id")
    _add_revision_argument(profile_update)
    profile_update.add_argument(
        "--name", dest="display_name", default=argparse.SUPPRESS
    )
    for field, option in _PROFILE_TEXT_OPTIONS:
        _add_optional_clearable_text(profile_update, field=field, option=option)
    terminal = profile_update.add_mutually_exclusive_group()
    terminal.add_argument(
        "--terminal-mode",
        choices=("auto", "managed", "direct"),
        dest="terminal_mode_hint",
        default=argparse.SUPPRESS,
    )
    terminal.add_argument(
        "--clear-terminal-mode",
        action="store_const",
        const=None,
        dest="terminal_mode_hint",
        default=argparse.SUPPRESS,
    )
    profile_update.add_argument("--dry-run", action="store_true")
    profile_update.add_argument("--json", action="store_true")
    profile_update.set_defaults(handler="_handle_project_profile_update")

    profile_delete = profile_subparsers.add_parser("delete")
    profile_delete.add_argument("launch_profile_id")
    _add_revision_argument(profile_delete)
    profile_delete.add_argument("--dry-run", action="store_true")
    profile_delete.add_argument("--json", action="store_true")
    profile_delete.set_defaults(handler="_handle_project_profile_delete")


_PROFILE_TEXT_OPTIONS = (
    ("agent_hint", "agent-hint"),
    ("structured_route_hint", "structured-route-hint"),
    ("model_hint", "model-hint"),
    ("mode_hint", "mode-hint"),
    ("host_hint", "host-hint"),
    ("workspace_policy_hint", "workspace-policy-hint"),
)


def _add_revision_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-revision", type=int, default=None)


def _add_profile_create_arguments(parser: argparse.ArgumentParser) -> None:
    for field, option in _PROFILE_TEXT_OPTIONS:
        parser.add_argument(f"--{option}", dest=field, default=None)
    parser.add_argument(
        "--terminal-mode",
        choices=("auto", "managed", "direct"),
        dest="terminal_mode_hint",
        default=None,
    )


def _add_optional_clearable_text(
    parser: argparse.ArgumentParser,
    *,
    field: str,
    option: str,
) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        f"--{option}",
        dest=field,
        default=argparse.SUPPRESS,
    )
    group.add_argument(
        f"--clear-{option}",
        action="store_const",
        const=None,
        dest=field,
        default=argparse.SUPPRESS,
    )


__all__ = ["register_project_catalog_commands"]
